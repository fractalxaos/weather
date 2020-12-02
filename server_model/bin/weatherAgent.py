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
# Copyright 2017 Jeff Owrey
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
#   * v10 15 Sep 2015 by J L Owrey; first release
#   * v12 08 Jan 2016 by J L Owrey; modified to use wind direction expressed
#         as compass points, improved device status checking
#   * v13 22 Nov 2017 by J L Owrey; added data validation rules to
#         convertData function to handle occasionally data glitches from the
#         weather station; improved diagnostic output; fixed bugs
#   * v14 03 Mar 2018 by J L Owrey; improved weather station online status
#         handling; improved code readability and comments
#   * v15 11 Nov 2018 by J L Owrey; improved system fault handling and data
#         conversion error handling
#   * v20 02 Dec 2020 by J L Owrey; ported to python 3; modified to server
#         model where the weather station acts in the role of http server
#         and network clients request data from weather station
#
from urllib.request import urlopen
import os
import sys
import signal
import subprocess
import multiprocessing
import time

_USER = os.environ['USER']

   ### DEFAULT WEATHER STATION URL ###

_DEFAULT_WEATHER_STATION_URL = "{your device url or ip address}"

    ### FILE AND FOLDER LOCATIONS ###

# get the user running this script
_USER = os.environ['USER']
# html document root directory
_DOCROOT_DIRECTORY = '/home/%s/public_html/weather/' % _USER
# location of weather charts used by html documents
_CHARTS_DIRECTORY = _DOCROOT_DIRECTORY + 'dynamic/'
# data file for mirror server
_DATA_FORWARDING_FILE = \
        "/home/%s/public_html/weather/dynamic/wea.dat" % _USER
# data for use by other services and html documents
_OUTPUT_DATA_FILE = _DOCROOT_DIRECTORY + 'dynamic/weatherData.js'
# rrdtool database file
_RRD_FILE = '/home/%s/database/weatherData.rrd' % _USER

    ### GLOBAL CONSTANTS ###

# weather station PIN
_STATION_PIN = '12345'
# number seconds to wait for a response to HTTP request
_HTTP_REQUEST_TIMEOUT = 2
# maximum number of failed updates from weather station
_MAX_FAILED_UPDATE_COUNT = 3
# web page data item refresh rate (sec)
_DEFAULT_DATA_UPDATE_INTERVAL = 10
# rrdtool database update rate (sec)
_DATABASE_UPDATE_INTERVAL = 60
# generation rate of day charts (sec)
_DAY_CHART_UPDATE_INTERVAL = 300
# generation rate of long period charts (sec)
_LONG_CHART_UPDATE_INTERVAL = 3600
# standard chart width in pixels
_CHART_WIDTH = 600
# standard chart height in pixels
_CHART_HEIGHT = 150

   ### CONVERSION FACTORS ###

# inches Hg per Pascal
_PASCAL_CONVERSION_FACTOR = 0.00029530099194
# correction for 253 feet elevation above sea level
_BAROMETRIC_PRESSURE_CORRECTION = 0.2767
# conversion of light sensor to percentage value
_LIGHT_SENSOR_FACTOR = 3.1

   ### GLOBAL VARIABLES ###

# turns on or off extensive debugging messages
#   can be modified by command line arguments
debugOption = False
verboseDebug = False
reportUpdateFails = False

# used for testing midnight reset feature
testResetOffsetSec = -1

# Periodicity of http requests sent to the weather station
#   can be modified by command line argument
dataUpdateInterval = _DEFAULT_DATA_UPDATE_INTERVAL
# weather station url
#   can be modified by command line argument
weatherStationUrl = _DEFAULT_WEATHER_STATION_URL
# weather station maintenance command
maintenanceCommand = ""

# used for detecting system faults and weather station online
# or offline status
failedUpdateCount = 0
previousUpdateTime = 0
stationOnline = True

    ### HELPER FUNCTIONS ###

def getTimeStamp():
    """Sets the error message time stamp to the local system time.
       Parameters: none
       Returns: string containing the time stamp
    """
    return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
##end def

def getEpochSeconds(sTime):
    """Convert the time stamp supplied in the weather data string
       to seconds since 1/1/1970 00:00:00.
       Parameters: 
           sTime - the time stamp to be converted must be formatted
                   as %m/%d/%Y %H:%M:%S
       Returns: epoch seconds
    """
    try:
        t_sTime = time.strptime(sTime, '%m/%d/%Y %H:%M:%S')
    except Exception as exError:
        print('%s getEpochSeconds: %s' % (getTimeStamp(), exError))
        return None
    tSeconds = int(time.mktime(t_sTime))
    return tSeconds
##end def

def setStatusToOffline():
    """Set weather station status to offline.  Removing the output data
       file causes the client web page to show that the weather station
       is offline.
       Parameters: none
       Returns: nothing
    """
    global stationOnline

    if stationOnline:
        print('%s station offline' % getTimeStamp())
    stationOnline = False
    if os.path.exists(_DATA_FORWARDING_FILE):
        os.remove(_DATA_FORWARDING_FILE)
    if os.path.exists(_OUTPUT_DATA_FILE):
        os.remove(_OUTPUT_DATA_FILE)
 ##end def

def terminateAgentProcess(signal, frame):
    """Send message to log when process killed
       Parameters: signal, frame - sigint parameters
       Returns: nothing
    """
    setStatusToOffline()
    print('%s terminating weather agent process' % \
              (getTimeStamp()))
    sys.exit(0)
##end def

    ### PUBLIC FUNCTIONS ###

def getWeatherData():
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

    try:
        currentTime = time.time()
        sUrl = weatherStationUrl + maintenanceCommand
        response = urlopen(sUrl, timeout=_HTTP_REQUEST_TIMEOUT)
        requestTime = time.time() - currentTime
        if verboseDebug:
            print("http request: %.4f seconds" % requestTime)

        # Format received data into a single string.
        httpContent =  response.read().decode('utf-8')
        content = ""
        for line in httpContent:
            content += line.replace('\n', '')
        del response

    except Exception as exError:
        # If no response is received from the device, then assume that
        # the device is down or unavailable over the network.  In
        # that case return None to the calling function.
        if debugOption:
            print("http error: %s" % exError)
        return None
    ##end try

    if verboseDebug:
        print(content)
        pass
    
    # If reset command was sent to weather station then allow extra time
    # for the station to reset and re-acquire the wifi access point.
    if maintenanceCommand.find('/r') > -1:
        if content.find('ok') > -1:
            maintenanceCommand = ''
            time.sleep(20)
        #return None

    return content
##end def

def parseInputDataString(sData, dData):
    """Parse the weather station data string into its component parts.  
       Parameters:
           sData - the string containing the data to be parsed
           dData - a dictionary object to contain the parsed data items
       Returns true if successful, false otherwise.
    """
    # Example input string
    #    $,ws=3.3,wd=12,ws2=2.5,wd2=11,wgs=6,wgd=12,wgs10=5,wgd10=14,
    #    h=73.4,t=58.5,p=101189.0,r=0.00,dr=0.00,b=3.94,l=1.1,#
    #
    try:
        sTmp = sData[2:-2]
        lsTmp = sTmp.split(',')
    except Exception as exError:
        print("%s parse failed: %s" % (getTimeStamp(), exError))
        return False

    # Load the parsed data into a dictionary object for easy access.
    for item in lsTmp:
        if "=" in item:
            dData[item.split('=')[0]] = item.split('=')[1]
    # Add date and status to dictionary object
    dData['status'] = 'online'
    dData['date'] = getTimeStamp()

    if len(dData) != 17:
        print("%s parse failed: corrupted data string" % (getTimeStamp()))
        return False;

    return True
##end def

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

        # Convert ambient light level to percent
        lightPct = int(100.0 * float(dData['l']) / _LIGHT_SENSOR_FACTOR)
        dData['l'] = '%d' % lightPct # format for web page

        # Validate converted pressure
        if pressureBar < 25.0 or pressureBar > 35.0:
            raise Exception('invalid pressure: %.4e - discarding' % pressureBar)
 
        # Validate temperature
        tempf = float(dData['t'])
        if tempf < -100.0:
            #maintenanceCommand = '/' + _STATION_PIN + '/r'
            raise Exception('invalid temperature: %.4e - discarding' \
                % tempf)

        # Validate humidity
        humidity = float(dData['h'])
        if humidity > 150.0:
            #print('%s invalid humidity: %.4e - discarding' % \
            #      (getTimeStamp(), humidity))
            #humidity = 100.0
            #dData['h'] = str(humidity)
            raise Exception('invalid humidity: %.4e - discarding' \
                % humidity)
            
        # Replace key names with their long form name.
        dData['windspeedmph'] = dData.pop('ws')
        dData['winddir'] = dData.pop('wd') 
        dData['windgustmph'] = dData.pop('gs')
        dData['windgustdir'] = dData.pop('gd')
        dData['windspeedmph_avg2m'] = dData.pop('ws2')
        dData['winddir_avg2m'] = dData.pop('wd2')
        dData['windgustmph_10m'] = dData.pop('gs10')
        dData['windgustdir_10m'] = dData.pop('gd10')
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
    ##end try

    return True
##end def

def verifyTimestamp(dData):
    """Check to see if the time stamped data is updating at the weather
       station update rate.  If the timestamp is not getting updated,
       then assume that the weather station is down or not available
       via the network.
       Parameters: 
           dData - dictionary object containing parsed weather data
       Returns: True if successful, False otherwise
    """
    global previousUpdateTime
    
    # Compare with the time stamp from the previous update.
    # If they are equal for more than a specified amount of time, it means
    # that the weather station data has not been updated, and that the
    # weather station is offline or unreachable.
    
    currentUpdateTime = getEpochSeconds(dData['date'])

    if (currentUpdateTime == previousUpdateTime):
        return False
    else:
        previousUpdateTime = currentUpdateTime
    return True
##end def

def setStationStatus(updateSuccess):
    """Detect if radiation monitor is offline or not available on
       the network. After a set number of attempts to get data
       from the monitor set a flag that the station is offline.
       Parameters:
           updateSuccess - a boolean that is True if data request
                           successful, False otherwise
       Returns: nothing
    """
    global failedUpdateCount, stationOnline

    if updateSuccess:
        failedUpdateCount = 0
        # Set status and send a message to the log if the station was
        # previously offline and is now online.
        if not stationOnline:
            print('%s station online' % getTimeStamp())
            stationOnline = True
        if debugOption:
            print('%s update successful' % getTimeStamp())
    else:
        # The last attempt failed, so update the failed attempts
        # count.
        failedUpdateCount += 1
        if debugOption or reportUpdateFails:
           print('%s update failed' % getTimeStamp())

    if failedUpdateCount >= _MAX_FAILED_UPDATE_COUNT:
        # Max number of failed data requests, so set
        # monitor status to offline.
        setStatusToOffline()
##end def

def writeOutputDataFile(dData, sOutputDataFile):
    """Writes to a file a formatted string containing the weather data.
       The file is written to the document dynamic data folder for use
       by html documents.
       Parameters: 
           dData - dictionary object containing weather data
           sOutputDataFile - the file to which to write the data
       Returns: True if successful, False otherwise
    """
    # Format the weather data as string using java script object notation.
    sData = '[{'
    for key in dData:
        sData += '\"%s\":\"%s\",' % (key, dData[key])
    sData = sData[:-1] + '}]\n'

    if verboseDebug:
        print(sData)
        pass

    # Write the string to the output data file for use by html documents.
    try:
        fc = open(sOutputDataFile, 'w')
        fc.write(sData)
        fc.close()
    except Exception as exError:
        print('%s writeOutputDataFile: %s' % (getTimeStamp(), exError))
        return False
    return True
##end def

def writeForwardingFile(sData):
    # Write the string to the output data file for use by html documents.
    try:
        fc = open(_DATA_FORWARDING_FILE, "w")
        fc.write(sData)
        fc.close()
    except Exception as exError:
        print("%s writeOutputDataFile: %s" % (getTimeStamp(), exError))
        return False
    return True
##end def

def checkForMidnight():
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
    if secondsSinceMidnight < dataUpdateInterval:
        if debugOption or True:
            print('%s sending midnight reset signal' % \
                 (getTimeStamp()))
        maintenanceCommand = '/' + _STATION_PIN + '/r'
    return
##end def

    ### DATABASE FUNCTIONS ###

def updateDatabase(dData):
    """Updates the rrdtool round robin database with data supplied in
       the weather data string.
       Parameters:
           dData - dictionary object containing items to be written to the
                   rrdtool database
       Returns: True if successful, False otherwise
    """
   # Get the data items to be stored in rrdtool database, and
   # convert string items to floating point numbers as necessary.
    try:
        time = getEpochSeconds(dData['date'])
        winddir = float(dData['winddir_avg2m']) * 22.5
    # Trap any data conversion errors.
    except Exception as exError:
        print('%s updateDatabase error: %s' % (getTimeStamp(), exError))
        return False

    # Format the rrdtool update command.
    strCmd = 'rrdtool update %s %s:%s:%s:%s:%s:%s:%s' % \
                  (_RRD_FILE, time, dData['windspeedmph_avg2m'], winddir,
                   dData['tempf'], dData['rainin'], dData['pressure'],
                   dData['humidity'])
    # Trap any data conversion errors
    if verboseDebug:
        print('%s\n' % strCmd) # DEBUG

    # Run the formatted command as a subprocess.
    try:
        subprocess.check_output(strCmd, stderr=subprocess.STDOUT, \
                                shell=True)
    except subprocess.CalledProcessError as exError:
        print('%s rrdtool update failed: %s' % \
              (getTimeStamp(), exError.output))
        return False
    else:
        if debugOption:
            print('database update successful')
    return True
##end def

def createAutoGraph(fileName, dataItem, gLabel, gTitle, gStart,
                    lower, upper, addTrend, autoScale):
    """Uses rrdtool to create a graph of specified weather data item.
       Graphs are for display in html documents.
       Parameters:
           fileName - name of graph file
           dataItem - the weather data item to be graphed
           gLabel - string containing a graph label for the data item
           gTitle - string containing a title for the graph
           gStart - time from now when graph starts
           lower - lower bound for graph ordinate
           upper - upper bound for graph ordinate
           addTrend - 0, show only graph data
                      1, show only a trend line
                      2, show a trend line and the graph data
           autoScale - if True, then use vertical axis auto scaling
               (lower and upper parameters are ignored), otherwise use
               lower and upper parameters to set vertical axis scale
       Returns: True if successful, False otherwise
    """
    gPath = _CHARTS_DIRECTORY + fileName + '.png'

    # Format the rrdtool graph command.

    # Set chart start time, height, and width.
    strCmd = 'rrdtool graph %s -a PNG -s %s -e \'now\' -w %s -h %s ' \
             % (gPath, gStart, _CHART_WIDTH, _CHART_HEIGHT)
   
    # Set the range and scaling of the chart y-axis.
    if lower < upper:
        strCmd  +=  '-l %s -u %s -r ' % (lower, upper)
    elif autoScale:
        strCmd += '-A '
    strCmd += '-Y '

    # Set the chart ordinate label and chart title. 
    strCmd += '-v %s -t %s ' % (gLabel, gTitle)

    # Show the data, or a moving average trend line, or both.
    strCmd += 'DEF:dSeries=%s:%s:AVERAGE ' % (_RRD_FILE, dataItem)
    if addTrend == 0:
        strCmd += 'LINE2:dSeries#0400ff '
    elif addTrend == 1:
        strCmd += 'CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#0400ff '
    elif addTrend == 2:
        strCmd += 'LINE1:dSeries#0400ff '
        strCmd += 'CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#0400ff '
    
    # if wind plot show color coded wind direction
    if dataItem == 'windspeedmph':
        strCmd += 'DEF:wDir=%s:winddir:AVERAGE ' % (_RRD_FILE)
        strCmd += 'VDEF:wMax=dSeries,MAXIMUM '
        strCmd += 'CDEF:wMaxScaled=dSeries,0,*,wMax,+,-0.15,* '
        strCmd += 'CDEF:ndir=wDir,337.5,GE,wDir,22.5,LE,+,wMaxScaled,0,IF '
        strCmd += 'CDEF:nedir=wDir,22.5,GT,wDir,67.5,LT,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:edir=wDir,67.5,GE,wDir,112.5,LE,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:sedir=wDir,112.5,GT,wDir,157.5,LT,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:sdir=wDir,157.5,GE,wDir,202.5,LE,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:swdir=wDir,202.5,GT,wDir,247.5,LT,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:wdir=wDir,247.5,GE,wDir,292.5,LE,*,wMaxScaled,0,IF '
        strCmd += 'CDEF:nwdir=wDir,292.5,GT,wDir,337.5,LT,*,wMaxScaled,0,IF '
  
        strCmd += 'AREA:ndir#0000FF:N '    # Blue
        strCmd += 'AREA:nedir#1E90FF:NE '  # DodgerBlue
        strCmd += 'AREA:edir#00FFFF:E '    # Cyan
        strCmd += 'AREA:sedir#00FF00:SE '  # Lime
        strCmd += 'AREA:sdir#FFFF00:S '    # Yellow
        strCmd += 'AREA:swdir#FF8C00:SW '  # DarkOrange 
        strCmd += 'AREA:wdir#FF0000:W '    # Red
        strCmd += 'AREA:nwdir#FF00FF:NW '  # Magenta
    ##end if
     
    if verboseDebug:
        print('%s\n' % strCmd) # DEBUG
    
    # Run the formatted rrdtool command as a subprocess.
    try:
        result = subprocess.check_output(strCmd, \
                     stderr=subprocess.STDOUT,   \
                     shell=True)
    except subprocess.CalledProcessError as exError:
        print('rrdtool graph failed: %s' % (exError.output))
        return False

    if debugOption:
        print('rrdtool graph: %s' % result.decode('utf-8'))

    return True
##end def

def generateDayGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    createAutoGraph('1d_windspeedmph', 'windspeedmph', 'miles\ per\ hour', \
                'Sustained\ Wind', 'now-1d', 0, 0, 0, True)
    createAutoGraph('1d_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'now-1d', 0, 0, 0, True)
    createAutoGraph('1d_pressure', 'pressure', 'inches\ Hg', \
                'Barometric\ Pressure', 'now-1d', 0, 0, 0, True)
    createAutoGraph('1d_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'now-1d', 0, 0, 0, True)
    createAutoGraph('1d_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'now-1d', 0, 0, 0, False)
##end def

def generateLongGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    # 10 day long graphs
    createAutoGraph('10d_windspeedmph', 'windspeedmph', 'miles\ per\ hour', \
                'Sustained\ Wind', 'end-10days', 0, 0, 0, True)
    createAutoGraph('10d_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-10days',0, 0, 0, True)
    createAutoGraph('10d_pressure', 'pressure', 'inches\ Hg', \
                'Barometric\ Pressure', 'end-10days',0, 0, 0, True)
    createAutoGraph('10d_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-10days', 0, 0, 0, True)
    createAutoGraph('10d_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-10days', 0, 0, 0, False)

    # 3 month long graphs
    createAutoGraph('3m_windspeedmph', 'windspeedmph', 'miles\ per\ hour', \
                'Sustained\ Wind', 'end-3months', 0, 0, 2, True)
    createAutoGraph('3m_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-3months',0, 0, 2, True)
    createAutoGraph('3m_pressure', 'pressure', 'inches\ Hg', \
#                'Barometric\ Pressure', 'end-3months', 29.0, 30.8, 2, True)
                'Barometric\ Pressure', 'end-3months', 0, 0, 2, True)
    createAutoGraph('3m_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-3months', 0, 0, 2, True)
    createAutoGraph('3m_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-3months', 0, 0, 0, False)

    # 12 month long graphs
    createAutoGraph('12m_windspeedmph', 'windspeedmph', 'miles\ per\ hour', \
                'Sustained\ Wind', 'end-12months', 0, 0, 1, True)
    createAutoGraph('12m_tempf', 'tempf', 'degrees\ Fahrenheit', \
                'Temperature', 'end-12months',0, 0, 1, True)
    createAutoGraph('12m_pressure', 'pressure', 'inches\ Hg', \
#                'Barometric\ Pressure', 'end-12months', 29.0, 30.8, 1, True)
                'Barometric\ Pressure', 'end-12months', 0, 0, 1, True)
    createAutoGraph('12m_humidity', 'humidity', 'percent', \
                'Relative\ Humidity', 'end-12months', 0, 0, 1, True)
    createAutoGraph('12m_rainin', 'rainin', 'inches', \
                'Rain\ Fall', 'end-12months', 0, 0, 0, False)
##end def

def getCLarguments():
    """Get command line arguments - there are two possible arguments
          -d turns on debug mode
          -v turns on verbose debug mode
          -r reports update fails
          -t sets the update poll interval in seconds (default=10)
          -u sets the weather station url
       Returns: nothing
    """
    global debugOption, verboseDebug, dataUpdateInterval
    global reportUpdateFails, weatherStationUrl

    index = 1
    while index < len(sys.argv):

        # Debug and error reporting options
        if sys.argv[index] == '-d':
            debugOption = True
        elif sys.argv[index] == '-v':
            debugOption = True
            verboseDebug = True
        elif sys.argv[index] == '-r':
            reportUpdateFails = True

        # Update period and url options
        elif sys.argv[index] == '-t':
            try:
                tempVal = float(sys.argv[index + 1])
            except:
                print('invalid update period')
                exit(-1)
            dataUpdateInterval = tempVal
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
        index += 1
    ##end while
    return
##end def

    ### SOFTWARE TEST FUNCTIONS ###

def testMidnightResetFeature():
    """Simple routine for testing station midnight reset feature by forcing
       the transmission of a reset signal 60 seconds after this program starts.
       Parameters:
           none
       Returns:
           nothing
    """
    global testResetOffsetSec

    now = time.localtime()
    testResetOffsetSec = 30 + now.tm_hour * 3600 + now.tm_min * 60 + \
                         now.tm_sec
##end def

     ### MAIN ROUTINE ###

def main():
    """Executive routine which manages timing and execution of all other
       events.
       Parameters: none
       Returns nothing.
    """
    # uncomment to test midnight reset feature
    #testMidnightResetFeature()

    print('%s starting up weather agent process' % \
                  (getTimeStamp()))

    signal.signal(signal.SIGTERM, terminateAgentProcess)

    # Get the command line arguments.
    getCLarguments()

    # last time the data file to the web server updated
    lastCheckForUpdateTime = -1
    # last time day charts were generated
    lastDayChartUpdateTime = -1
    # last time long term charts were generated
    lastLongChartUpdateTime = -1
    # last time the rrdtool database updated
    lastDatabaseUpdateTime = -1

    ## Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print('rrdtool database does not exist\n' \
              'use createWeatherRrd script to ' \
              'create rrdtool database\n')
        exit(1)
 
    ## main loop
    while True:

        currentTime = time.time() # get current time in seconds

        # After every weather station data transmission, get and process
        # weather station data from the local file that gets updated by the
        # submit.php script.
        if currentTime - lastCheckForUpdateTime > dataUpdateInterval:
            lastCheckForUpdateTime = currentTime
            dData = {}
            result = True
 
            # At midnight send the reset signal to the weather station.
            checkForMidnight()

            # Read the input data file from the submit.php script which
            # processes data from the weather station.
            sData = getWeatherData()

            # If no data received, then do not proceed any further.
            if sData == 'ok':
                continue
            elif sData == None:
                result = False

            # Upon successfully getting the data, parse the data.
            if result:
                 result = parseInputDataString(sData, dData)

            # Verify that the weather station is still online.
            if result:
                 result = verifyTimestamp(dData)

            # If the station is online and the data successfully parsed, 
            # then convert the data.
            if result:
                result = convertData(dData)

            # If the data successfully converted, then the write the data
            # to the output data file.
            if result:
               writeOutputDataFile(dData, _OUTPUT_DATA_FILE)
               writeForwardingFile(sData)

            # At the rrdtool database update interval write the data to
            # the rrdtool database.
            if result and (currentTime - lastDatabaseUpdateTime > \
                   _DATABASE_UPDATE_INTERVAL):   
                lastDatabaseUpdateTime = currentTime
                # Update the round robin database with the parsed data.
                result = updateDatabase(dData)

            # Set the station status to online or offline depending on the
            # success or failure of the above operations.
            setStationStatus(result)
        ##end if

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
        if debugOption and not verboseDebug:
            #print()
            pass
        if debugOption:
            print('processing time: %6f sec\n' % elapsedTime)
        remainingTime = dataUpdateInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
    return
##end def

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n', end='')
        terminateAgentProcess('KeyboardInterrupt','Module')

##end module
