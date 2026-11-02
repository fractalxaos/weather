#!/usr/bin/python3 -u
## The -u option turns off block buffering of python output. This assures
## that error messages get printed to the log file as they happen.
#  
# Module: weatherAgent.py
#
# Description: This module acts as an agent between the weather station and
# the web services.  The agent works in conjunction with the submit.php script
# and the http service to collect the data transmitted by the weather station.
# The weather station periodically sends weather data to the http service via
# the http GET method.  The submit.php script writes the data, along
# with a time stamp, to a JSON formatted file.  The agent periodically reads
# this file and uses the data in the file to perform a number of operations:
#     - convert units of various weather data items
#     - verify the time stamp to determine device on or off line status
#     - update the weather maintenance signal file (initiates a
#       daily maintenance reset of the weather station)
#     - update a round robin (rrdtool) database with the weather data
#     - periodically generate graphic charts for display in html documents
#     - forward the weather data to other services, such as Wunderground
#     - write the processed weather data to a JSON file for use by
#       html documents
#       
# Copyright 2021 Jeff Owrey
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public Licensef
#    along with this program.  If not, see http://www.gnu.org/license.
#
# Revision History
#   * v30 17 Oct 2021 by J L Owrey; first release
#
#2345678901234567890123456789012345678901234567890123456789012345678901234567890

import os
import sys
import signal
import multiprocessing
import time
import json
from urllib.request import urlopen
import rrdbase

   ### ENVIRONMENT ###

_USER = os.environ['USER']
_SERVER_MODE = "primary"
_STATION_PIN = '12345' # weather station PIN

   ### DEFAULT WEATHER STATION URL ###

_DEFAULT_WEATHER_STATION_URL = "{your weather station URL}"

    ### FILE AND FOLDER LOCATIONS ###

# get the user running this script
_USER = os.environ['USER']
# html document root directory
_DOCROOT_DIRECTORY = '/home/%s/public_html/weather/' % _USER
# location of weather charts used by html documents
_CHARTS_DIRECTORY = _DOCROOT_DIRECTORY + 'dynamic/'
# data for use by other services and html documents
_OUTPUT_DATA_FILE = _DOCROOT_DIRECTORY + 'dynamic/weatherData.js'
# rrdtool database file
_RRD_FILE = '/home/%s/database/weatherData.rrd' % _USER

    ### GLOBAL CONSTANTS ###

# maximum number of failed data requests allowed
_MAX_FAILED_DATA_REQUESTS = 2
# maximum number of http request retries  allowed
_MAX_HTTP_RETRIES = 5
# delay time between http request retries
_HTTP_RETRY_DELAY = 2.1199
# interval in seconds between data requests
_DEFAULT_DATA_REQUEST_INTERVAL = 60
# number seconds to wait for a response to HTTP request
_HTTP_REQUEST_TIMEOUT = 3

# interval in seconds between database updates
_DATABASE_UPDATE_INTERVAL = 60
# interval in seconds between day chart updates
_DAY_CHART_UPDATE_INTERVAL = 300
# interval in seconds between long term chart updates
_LONG_CHART_UPDATE_INTERVAL = 3600
# standard chart width in pixels
_CHART_WIDTH = 600
# standard chart height in pixels
_CHART_HEIGHT = 150

# seconds waited after midnight reset before next data request
_MIDNIGHT_RESET_HOLDOFF = 50

   ### CONVERSION FACTORS ###

# inches Hg per Pascal
_PASCAL_CONVERSION_FACTOR = 0.00029530099194
# correction for 253 feet elevation above sea level
#_BAROMETRIC_PRESSURE_CORRECTION = 0.0678
_BAROMETRIC_PRESSURE_CORRECTION = 0.2278
# humidity correction offsed
_HUMIDITY_CORRECTION = 10.0
# conversion of light sensor to percentage value
_MIN_LIGHT_LVL = 2.7
_MAX_LIGHT_LVL = 3.2

   ### GLOBAL VARIABLES ###

# Turns on or off extensive debugging messages.
# Can be modified by command line arguments.
verboseMode = False
debugMode = False
reportUpdateFails = False
testMidnightReset = False

# used for detecting system faults and weather station online
# or offline status
failedUpdateCount = 0
httpRetries = 0
stationOnline = False

# periodicity of http requests sent to the weather station
#   can be modified by command line argument
dataRequestInterval = _DEFAULT_DATA_REQUEST_INTERVAL
# weather station url
#   can be modified by command line argument
weatherStationUrl = _DEFAULT_WEATHER_STATION_URL
# used for testing midnight reset feature
testResetOffsetSec = -1
# weather station maintenance command
maintenanceCommand = ''
# global object for rrdtool database functions
rrdb = None

    ### HELPER FUNCTIONS ###

def getTimeStamp():
    """Sets the error message time stamp to the local system time.
       Parameters: none
       Returns: string containing the time stamp
    """
    return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
## end def

def setStatusToOffline():
    """Set weather station status to offline.  Removing the output data
       file causes the client web page to show that the weather station
       is offline.
       Parameters: none
       Returns: nothing
    """
    global stationOnline

    # Inform downstream clients by removing output data file.
    if os.path.exists(_OUTPUT_DATA_FILE):
       os.remove(_OUTPUT_DATA_FILE)

    if stationOnline:
        print('%s weather station offline' % getTimeStamp())
    stationOnline = False
## end def

def terminateAgentProcess(signal, frame):
    """Send message to log when process killed
       Parameters: signal, frame - sigint parameters
       Returns: nothing
    """
    # Inform downstream clients by removing output data file.
    if os.path.exists(_OUTPUT_DATA_FILE):
       os.remove(_OUTPUT_DATA_FILE)
    print('%s terminating weather agent process' % \
              (getTimeStamp()))
    sys.exit(0)
## end def

def verifyMidnightReset(dData):
    global maintenanceCommand
    # If reset command was sent to weather station then allow extra time
    # for the station to reset and re-acquire the wifi access point.
    #if dData['content'].find('ok') > -1:
    if dData['content'] == 'ok':
        maintenanceCommand = ''
        time.sleep(_MIDNIGHT_RESET_HOLDOFF)
        if verboseMode:
            print("%s midnight reset successful" % getTimeStamp())
        return True
    else:
        print("%s midnight reset failed: resending" % getTimeStamp())
        return False
    ## end if
## end def

def testMidnightResetFeature():
    """Simple routine for testing station midnight reset feature by forcing
       the transmission of a reset signal 60 seconds after this program
       starts.
       Parameters:
           none
       Returns:
           nothing
    """
    global testResetOffsetSec
    _MIDNIGHT_RESET_DELAY = 120

    now = time.localtime()
    testResetOffsetSec = _MIDNIGHT_RESET_DELAY + now.tm_hour * 3600 + \
        now.tm_min * 60 + now.tm_sec
## end def

    ### PUBLIC FUNCTIONS ###

def getWeatherData(dData):
    """Send http request to the weather station.  The response
       contains the weather data, formatted
       as an html document.
    Parameters: 
        weatherStationUrl - url of radiation monitoring device
        HttpRequesttimeout - how long to wait for device
                             to respond to http request
    Returns a string containing the radiation data, or None if
    not successful.
    """
    global maintenanceCommand
    global httpRetries

    sUrl = weatherStationUrl + maintenanceCommand

    try:
        currentTime = time.time()
        response = urlopen(sUrl, timeout=_HTTP_REQUEST_TIMEOUT)
        requestTime = time.time() - currentTime

        content = response.read().decode('utf-8')
        content = content.replace('\n', '')
        content = content.replace('\r', '')
        if content == "":
            raise Exception("empty response")

    except Exception as exError:
        # If no response is received from the device, then assume that
        # the device is down or unavailable over the network.  In
        # that case return None to the calling function.
        httpRetries += 1

        if reportUpdateFails:
            print("%s " % getTimeStamp(), end='')
        if reportUpdateFails or verboseMode:
            print("http request failed (%d): %s" % \
                (httpRetries, exError))

        if httpRetries > _MAX_HTTP_RETRIES:
            httpRetries = 0
            return False
        else:
            time.sleep(_HTTP_RETRY_DELAY)
            return getWeatherData(dData)
    ## end try

    if debugMode:
        print(content)
    if verboseMode:
        print("http request successful: "\
              "%.4f seconds" % requestTime)

    httpRetries = 0
    dData['content'] = content
    return True
## end def

def parseDataString(dData):
    """Parse the weather station data string into its component parts.  
       Parameters:
           sData - the string containing the data to be parsed
           dData - a dictionary object to contain the parsed data items
       Returns true if successful, false otherwise.
    """
    # Example input string
    #    $,h=73.4,t=58.5,p=101189.0,r=0.00,dr=0.00,b=3.94,l=1.1,#
    #
    try:
        sData = dData.pop('content')
        lData = sData[2:-2].split(',')
    except Exception as exError:
        print("%s parse failed: %s" % (getTimeStamp(), exError))
        return False
    
    # Verfy the expected number of data items have been received.
    if len(lData) != 7:
        print("%s parse failed: corrupted data string" % getTimeStamp())
        return False;

    # Load the parsed data into a dictionary object for easy access.
    for item in lData:
        if "=" in item:
            dData[item.split('=')[0]] = item.split('=')[1]

    # Add date and status to dictionary object
    dData['status'] = 'online'
    dData['date'] = getTimeStamp()
    dData['serverMode'] = _SERVER_MODE

    return True
## end def

def convertData(dData):
    """Convert individual weather data items as necessary.  Also
       format data items for use by html documents.  The keys
       created below are specific to the Weather Underground service.
       Parameters:
           dData - a dictionary object containing the data items to be
                   converted
       Returns: True if successful, False otherwise
    """

    try:
        # Convert pressure from pascals to inches Hg
        pressureBar = float(dData['p']) * _PASCAL_CONVERSION_FACTOR + \
                      _BAROMETRIC_PRESSURE_CORRECTION
        dData['p'] = '%.2f' % pressureBar # format for web page
        # Validate converted pressure
        if pressureBar < 25 or pressureBar > 35:
            raise Exception('invalid pressure: %.4e - discarding' % pressureBar)
 
        # Convert ambient light level to percent
        lightLvl = float(dData['l'])
        if lightLvl <= _MIN_LIGHT_LVL:
            lightPct = 0
        elif lightLvl >= _MAX_LIGHT_LVL:
            lightPct = 100
        else:
            lightPct = round(100.0 * (lightLvl - _MIN_LIGHT_LVL) / \
                       (_MAX_LIGHT_LVL - _MIN_LIGHT_LVL))
        dData['l'] = '%d' % lightPct # format for web page

 
        # Validate temperature
        tempf = float(dData['t'])
        #dData['t'] = '%d' % round(tempf)
        if tempf < -100:
            #maintenanceCommand = '/' + _STATION_PIN + '/r'
            raise Exception('invalid temperature: %.4e - discarding' % tempf)

        # Validate humidity
        humidity = float(dData['h']) - _HUMIDITY_CORRECTION
        dData['h'] = '%d' % round(humidity) 
        if humidity > 110:
            raise Exception('invalid humidity: %.4e - discarding' % humidity)
            
        # Replace key names with their long form name.
        dData['humidity'] = dData.pop('h')
        dData['tempf'] = dData.pop('t')
        dData['rainin'] = dData.pop('r')
        dData['dailyrainin'] = dData.pop('dr')
        dData['pressure'] = dData.pop('p')
        dData['batt_lvl'] = dData.pop('b')
        dData['light_lvl'] = dData.pop('l')

    # Trap any data conversion errors.
    except Exception as exError:
        print('%s conversion error: %s' % (getTimeStamp(), exError))
        return False
    ## end try

    return True
## end def

def writeOutputFile(dData):
    """Writes to a file a formatted string containing the weather data.
       The file is written to the document dynamic data folder for use
       by html documents.
       Parameters: 
           dData - dictionary object containing weather data
           sOutputDataFile - the file to which to write the data
       Returns: True if successful, False otherwise
    """

    # Format the weather data as string using java script object notation.
    jsData = json.loads("{}")
    try:
        for key in dData:
            jsData.update({key:dData[key]})
        sData = "[%s]" % json.dumps(jsData)
    except Exception as exError:
        print("%s writeOutputFile: %s" % (getTimeStamp(), exError))
        return False

    if debugMode:
        print(sData)

    # Write the string to the output data file for use by html documents.
    try:
        fc = open(_OUTPUT_DATA_FILE, 'w')
        fc.write(sData)
        fc.close()
    except Exception as exError:
        print('%s writeOutputFile: %s' % (getTimeStamp(), exError))
        return False
    return True
## end def

def setStationStatus(updateSuccess):
    """Detect if radiation monitor is offline or not available on
       the network. After a set number of attempts to get data
       from the monitor set a flag that the station is offline.
       Parameters:
           updateSuccess - a boolean that is True if data request
                           successful, False otherwise
       Returns: nothing
    """
    global failedUpdateCount, stationOnline, maintenanceCommand

    if updateSuccess:
        failedUpdateCount = 0
        # Set status and send a message to the log if the device
        # previously offline and is now online.
        if not stationOnline:
            print('%s weather station online' % getTimeStamp())
            stationOnline = True
        return
    else:
        # The last attempt failed, so update the failed attempts
        # count.
        failedUpdateCount += 1

    if failedUpdateCount == _MAX_FAILED_DATA_REQUESTS:
        # Max number of failed data requests, so set
        # device status to offline.
        maintenanceCommand = ''
        setStatusToOffline()
## end def

def midnightReset(dData):
    """Check the time to see if midnight has just occurred during the last
       device update cycle. If so, then send a reset message to the weather
       station.
       Parameters: none
       Returns: nothing
    """
    global maintenanceCommand

    # Get the number of seconds that have elapsed since midnight.
    now = time.localtime()
    secondsSinceMidnight = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    
    # Perform a test of the midnight reset feature if requested.
    if testResetOffsetSec > 0:
        secondsSinceMidnight = abs(testResetOffsetSec - secondsSinceMidnight)

    # If the number of elapsed seconds is smaller than the update interval
    # then midnight has just occurred and the weather station needs to be
    # sent its daily reset.
    if secondsSinceMidnight < dataRequestInterval:
        if verboseMode:
            print('%s sending midnight reset signal' % \
                 (getTimeStamp()))
        #maintenanceCommand = '/' + _STATION_PIN + '/r'
        maintenanceCommand = '/' + _STATION_PIN + '/b'
        # Send reset maintenance command. Return true if command
        # successful, or false otherwise.
        if not getWeatherData(dData):
            return False
        return verifyMidnightReset(dData)
    else:
        return True
    
## end def

    ### GRAPH FUNCTIONS ###

def generateDayGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    rrdb.createWeaGraph('1d_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_pressure', 'pressure', 'inches\ Hg', \
                'Barometric\ Pressure', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'now-1d', 0, 0, 0, False)
## end def

def generateLongGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    # 10 day long graphs
    rrdb.createWeaGraph('10d_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-10days',0, 0, 0, True)
    rrdb.createWeaGraph('10d_pressure', 'pressure', 'inches\ Hg', \
                'Barometric\ Pressure', 'end-10days',0, 0, 0, True)
    rrdb.createWeaGraph('10d_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-10days', 0, 0, 0, True)
    rrdb.createWeaGraph('10d_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-10days', 0, 0, 0, False)

    # 3 month long graphs
    rrdb.createWeaGraph('3m_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-3months',0, 0, 2, True)
    rrdb.createWeaGraph('3m_pressure', 'pressure', 'inches\ Hg', \
#                'Barometric\ Pressure', 'end-3months', 29.0, 30.8, 2, True)
                'Barometric\ Pressure', 'end-3months', 0, 0, 2, True)
    rrdb.createWeaGraph('3m_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-3months', 0, 0, 2, True)
    rrdb.createWeaGraph('3m_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-3months', 0, 0, 0, False)

    # 12 month long graphs
    rrdb.createWeaGraph('12m_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-12months',0, 0, 0, True)
    rrdb.createWeaGraph('12m_pressure', 'pressure', 'inches\ Hg', \
#                'Barometric\ Pressure', 'end-12months', 29.0, 30.8, 1, True)
                'Barometric\ Pressure', 'end-12months', 0, 0, 0, True)
    rrdb.createWeaGraph('12m_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-12months', 0, 0, 0, True)
    rrdb.createWeaGraph('12m_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-12months', 0, 0, 0, False)
## end def

def getCLarguments():
    """Get command line arguments - there are two possible arguments
          -d turns on debug mode
          -m test midnight reset procedure
          -p sets the update poll interval in seconds (default=60)
          -r report failed updates
          -u sets the weather station url
          -v turns on verbose mode
       Returns: nothing
    """
    global verboseMode, debugMode, dataRequestInterval
    global reportUpdateFails, weatherStationUrl, testMidnightReset

    index = 1
    while index < len(sys.argv):

        # Debug and error reporting options
        if sys.argv[index] == '-v':
            verboseMode = True
        elif sys.argv[index] == '-d':
            verboseMode = True
            debugMode = True
        elif sys.argv[index] == '-r':
            reportUpdateFails = True
        elif sys.argv[index] == '-m':
            testMidnightReset = True

        # Update period and url options
        elif sys.argv[index] == '-p':
            try:
                dataRequestInterval = abs(float(sys.argv[index + 1]))
            except:
                print("invalid polling period")
                exit(-1)
            index += 1
        elif sys.argv[index] == '-u':
            weatherStationUrl = sys.argv[index + 1]
            if weatherStationUrl.find('http://') < 0:
                weatherStationUrl = 'http://' + weatherStationUrl
            index += 1
        else:
            cmd_name = sys.argv[0].split('/')
            print("Usage: %s [-d|v|r] [-t seconds] [-u url]" % cmd_name[-1])
            exit(-1)
        ## end if
        index += 1
    ## end while
    return
## end def

     ### MAIN ROUTINE ###

def setup():
    """Executive routine which manages timing and execution of all other
       events.
       Parameters: none
       Returns nothing.
    """
    global rrdb

    print('=====================================================')
    print('%s starting up weather agent process' % \
                  (getTimeStamp()))

    # Get the command line arguments.
    getCLarguments()

    if testMidnightReset:
        testMidnightResetFeature()

    # Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print('rrdtool database does not exist\n' \
              'use createWeatherRrd script to ' \
              'create rrdtool database\n')
        exit(1)

    signal.signal(signal.SIGTERM, terminateAgentProcess)
    signal.signal(signal.SIGINT, terminateAgentProcess)

    # Define object for calling rrdtool database functions.
    rrdb = rrdbase.rrdbase( _RRD_FILE, _CHARTS_DIRECTORY, _CHART_WIDTH, \
                            _CHART_HEIGHT, verboseMode, debugMode )
## end def

def loop():
     # last time the data file to the web server updated
    lastDataRequestTime = -1
    # last time day charts were generated
    lastDayChartUpdateTime = -1
    # last time long term charts were generated
    lastLongChartUpdateTime = -1
    # last time the rrdtool database updated
    lastDatabaseUpdateTime = -1

    while True:

        currentTime = time.time() # get current time in seconds

        # Every data update interval request data from the weather
        # station and process the received data.
        if currentTime - lastDataRequestTime > dataRequestInterval:
            lastDataRequestTime = currentTime
            dData = {}
 
            # At midnight send the reset signal to the weather station.
            result = midnightReset(dData)

            # Send a request for weather data to the weather station.
            if result:
                result = getWeatherData(dData)

            # Upon successfully getting the data, parse the data.
            if result:
                 result = parseDataString(dData)

            # If the station is online and the data successfully parsed, 
            # then convert the data.
            if result:
                result = convertData(dData)

            # If the data successfully converted, then the write the data
            # to the output data file.
            if result:
               result = writeOutputFile(dData)

            # At the rrdtool database update interval write the data to
            # the rrdtool database.
            if result and (currentTime - lastDatabaseUpdateTime >
                           _DATABASE_UPDATE_INTERVAL):   
                lastDatabaseUpdateTime = currentTime
                # Update the round robin database with the parsed data
                # passed as a tuple.
                result = rrdb.updateDatabase(dData['date'], -1, -1, \
                         dData['tempf'], dData['rainin'], dData['pressure'], \
                         dData['humidity'])

            # Set the station status to online or offline depending on the
            # success or failure of the above operations.
            setStationStatus(result)
        ## end if

        # At the day chart generation interval generate day charts.
        if currentTime - lastDayChartUpdateTime > _DAY_CHART_UPDATE_INTERVAL:
            lastDayChartUpdateTime = currentTime
            p = multiprocessing.Process(target=generateDayGraphs, args=())
            p.start()

        # At daily intervals generate long time period charts.
        if currentTime - lastLongChartUpdateTime > _LONG_CHART_UPDATE_INTERVAL:
            lastLongChartUpdateTime = currentTime
            p = multiprocessing.Process(target=generateLongGraphs, args=())
            p.start()

        # Relinquish processing back to the operating system until
        # the next update interval.  Also provide a processing time
        # information for debugging and performance analysis.

        elapsedTime = time.time() - currentTime
        if result:
            if verboseMode:
                print("update successful: %6f sec\n"
                      % elapsedTime)
        else:
            print("%s update failed: %6f sec\n"
                      % (getTimeStamp(), elapsedTime))
        remainingTime = dataRequestInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
## end def

if __name__ == '__main__':
    setup()
    loop()

## end module
