#!/usr/bin/python3 -u
## The -u option turns off block buffering of python output. This assures
## that error messages get printed to the log file as they happen.
#  
# Module: weatherAgent.py
#
# Description: This module acts as an agent between the weather station and
# and Internet web services.  The agent receives data from a number of
# weather sensors and preforms the following operations
#     - convert units of various weather data items
#     - update a round robin (rrdtool) database with the weather data
#     - periodically generate graphic charts for display in html documents
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
#   * v10 20 Sep 2021 by J L Owrey: first release
#   * v11 12 Jun 2025 by J L Owrey: added handling for source url command line
#         parameter; removed get wind sensor data functions from getSenorData
#         function as this is no longer supported
#   * v12 15 Jul 2025 by J L Owrey: improved handling to source server vs
#         mirror server detection and handling.  Either the source server host
#         name or source server url can now be entered via the command line
#         'u' option.
#   * v13 26 May 2026 by J L Owrey: added command line parameter for specifying
#         the source server name
#
#2345678901234567890123456789012345678901234567890123456789012345678901234567890

# Import required python libraries.
import os
import sys
import signal
import multiprocessing
import time
import json
import rrdbase

   ### ENVIRONMENT ###

# Certain folder paths contain the user id
_USER = os.environ['USER']

# default source server url (change as necessary)
_DEFAULT_SOURCE_SERVER_URL = \
    "http://piweather.local/weather/dynamic/weatherData.js"
# default source server host name (change as necessary)
_DEFAULT_SOURCE_SERVER = "piweather"

    ### FILE AND FOLDER LOCATIONS ###

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
_MAX_FAILED_DATA_REQUESTS = 3
# interval in seconds between data requests
_DEFAULT_DATA_REQUEST_INTERVAL = 10
# time allowed for source server to respond to request for data
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

   ### CONVERSION FACTORS ###

# correction for 253 feet elevation above sea level
_BAROMETRIC_ALTITUDE_CORRECTION = 0.18 # 0.2767

   ### GLOBAL VARIABLES ###

# A "source" server gets data directly from sensors and makes
# data available to downstream servers.  A "mirror" server gets
# data from the upstream source server.  The default value may
# be changed by the setup routine if it does not detect sensors.
serverMode = None
# Host name and url for source server - can be modified
# by command line argument.
sourceUrl = None
sourceServer = None

# turns on or off extensive debugging messages
#   can be modified by command line argument
debugMode = False
verboseMode = False
reportUpdateFails = False

# used for detecting system faults and weather station online
# or offline status
failedUpdateCount = 0
deviceOnline = False

# Periodicity of http requests sent to the weather station
#   can be modified by command line argument
dataRequestInterval = _DEFAULT_DATA_REQUEST_INTERVAL

# used for testing midnight reset feature
testResetOffsetSec = -1

# Sensor instances
tph1 = None
ra1 = None

# object for calling rrdtool database functions.
rrdb = None

# Most recent error message (e.g., http, sensor, and database errors)
mostRecentError = ""

    ### HELPER FUNCTIONS ###

def getTimeStamp():
    """Sets the error message time stamp to the local system time.
       Parameters: none
       Returns: string containing the time stamp
    """
    return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
## end def

def setStatusToOffline():
    """Set the detected status of the device to
       "offline" and inform downstream clients by removing input
       and output data files.
       Parameters: none
       Returns: nothing
    """
    global deviceOnline

    # Inform downstream clients by removing output data file.
    if os.path.exists(_OUTPUT_DATA_FILE):
       os.remove(_OUTPUT_DATA_FILE)
    # If the weather station was previously online and now offline,
    # then send a message that we are now offline.
    if deviceOnline:
        print('%s %s' % (getTimeStamp(), mostRecentError))
        print('%s weather station offline' % getTimeStamp())
    deviceOnline = False
## end def

def terminateAgentProcess(signal, frame):
    """Send a message to log when the agent process gets killed
       by the operating system.  Inform downstream clients
       by removing input and output data files.
       Parameters:
           signal, frame - dummy parameters
       Returns: nothing
    """
    global mostRecentError
    
    mostRecentError = 'terminating weather agent process'
    setStatusToOffline()
    sys.exit(0)
##end def

def testMidnightResetFeature():
    """Simple routine for testing station midnight reset feature by forcing
       the transmission of a reset signal 60 seconds after this program starts.
       Parameters:
           none
       Returns:
           nothing
    """
    global testResetOffsetSec

    _RESET_DELAY_TIME = 55
    now = time.localtime()
    testResetOffsetSec = _RESET_DELAY_TIME + now.tm_hour * 3600 + \
                         now.tm_min * 60 + now.tm_sec
##end def

    ### PUBLIC FUNCTIONS ###

def getDataFromSourceServer(dData):
    """
    Called by mirror server to retrieve weather data from the primary
    server.  Converts json string to dictionary object.
    Parameters:
        dData - dictionary object to receive weather data items
    Returns:
        True if successful, False otherwise
    """
    global mostRecentError

    sUrl = sourceUrl
    if sUrl.find('http://') < 0:
        sUrl = 'http://' + sUrl

    try:
        currentTime = time.time()
        response = urlopen(sUrl, timeout=_HTTP_REQUEST_TIMEOUT)
        requestTime = time.time() - currentTime

        content =  response.read().decode('utf-8')
        content = content.replace('\n', '')
        content = content.replace('\r', '')
        if content == "":
            raise Exception("empty response")

    except Exception as exError:
        # If no response is received from the device, then assume that
        # the device is down or unavailable over the network.  In
        # that case return False to the calling function.
        if verboseMode or reportUpdateFails:
            print("%s (%d) http error: %s" \
                  % (getTimeStamp(), failedUpdateCount, exError))
        mostRecentError = exError
        return False
    
    if verboseMode:
        print('http request: %.4f seconds' % requestTime)

    content = eval(content[1:-1])

    for key in content:
        dData[key] = content[key]

    if debugMode:
       #print(dData)
       pass

    dData['serverMode'] = serverMode

    return True
    # end def

def getDataFromSensors(dData):
    """
    Poll sensors for data. Store the data in a dictionary object for
    use by other subroutines.  The dictionary object passed in should
    an empty dictionary, i.e., dData = { }.
    Parameters:
        dData - a dictionary object to contain the sensor data
    Returns:
        True if successful, False otherwise
    """
    dData["time"] = getTimeStamp()
    dData['serverMode'] = serverMode
 
    try:
        # Get TPH data.
        (dData["pressure"], dData["temp"]) = tph1.getPTdata(0x48, 0x58)
        dData["humidity"] = tph1.getHumidity()

        # Get rainfall data.
        ra1.updateHourlyRain()
        dData['hourly_rain'] = ra1.getHourlyRain()
        dData['daily_rain'] = ra1.getDailyRain()

        # Sensors may be added at a future date to collect this information.
        dData['windspeedmph_avg2m'] = -1
        dData['winddir_avg2m'] = -1

        # Get battery voltage.
        dData['batt_lvl'] = 'n/a'
    except Exception as exError:
        #if verboseMode or reportUpdateFails:
        print("%s (%d) sensor error: %s" \
                  % (getTimeStamp(), failedUpdateCount, exError))
        tph1.resetRH()
        tph1.resetPT()
        mostRecentError = exError
        return False

    dData['status'] = 'online'
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
    global mostRecentError

    try:
        # Convert pressure from millibars to inches Hg and correct barometric 
        # pressure for altitude.  Note pressure as reported by sensor must be
        # divided by a factor of 100.  Multiply by 0.02953 to convert millibars
        # to inches Mercury.
        pressure = float(dData['pressure']) * 0.02953 / 100 + \
                         _BAROMETRIC_ALTITUDE_CORRECTION
        dData['pressure'] = '%.2f' % pressure # format for web page
        # Validate converted pressure.
        if pressure < 25.0 or pressure > 35.0:
            raise Exception('invalid pressure: %.4e' % \
                pressure)
 
        # Convert temperature from Celcius to Fahrenheit.  Note pressure
        # as reported by sensor must be divided by a factor of 100.  Use
        # the formula Degrees(F) = (9/5)degrees(C) + 32. 
        tempf = float(dData.pop('temp')) * 1.8 / 100 + 32
        dData['tempf'] = '%.1f' % tempf
        # Validate converted temperature.
        if tempf < -100.0 or tempf > 200.0:
            raise Exception('invalid temperature: %.4e - discarding' \
                % tempf)

        # Validate humidity.
        humidity = float(dData['humidity'])
        dData['humidity'] = '%.0f' % humidity
        if humidity > 110.0:
            raise Exception('invalid humidity: %.4e - discarding' \
                % humidity)

        # Convert hourly rain.
        hourlyRain = float(dData['hourly_rain'])
        dData['hourly_rain'] = '%.3f' % hourlyRain

        # Convert daily rain.
        dailyRain = float(dData['daily_rain'])
        dData['daily_rain'] = '%.3f' % dailyRain

    # Trap any data conversion errors.
    except Exception as exError:
        print('%s conversion error: %s' % (getTimeStamp(), exError))
        mostRecentError = exError
        return False
    ##end try
    return True
##end def

def writeOutputFile(dData):
    """
    Write sensor data items to the output data file, formatted as 
    a Javascript file.  This file may then be requested and used by
    by downstream clients, for instance, an HTML document.
    Parameters:
        dData - a dictionary containing the data to be written
                   to the output data file
        Returns: True if successful, False otherwise
    """
    global mostRecentError

    # Write a JSON formatted file for use by html clients.  The following
    # data items are sent to the client file.
    #    * The last database update date and time
    #    * The data request interval
    #    * The sensor values

    # Create a JSON formatted string from the sensor data.
    jsData = json.loads("{}")
    try:
        for key in dData:
            jsData.update({key:dData[key]})
        sData = "[%s]" % json.dumps(jsData)
    except Exception as exError:
        print("%s writeOutputFile: %s" % (getTimeStamp(), exError))
        mostRecentError = exError
        return False

    if debugMode:
        print(sData)

    # Write the JSON formatted data to the output data file.

    try:
        fc = open(_OUTPUT_DATA_FILE, "w")
        fc.write(sData)
        fc.close()
    except Exception as exError:
        print("%s write output file failed: %s" % \
              (getTimeStamp(), exError))
        mostRecentError = exError
        return False

    return True
## end def

def setStatus(updateSuccess):
    """Detect if sensor read operation successful. After a set number
       of unsuccessful attempts to get data from the sensors set a flag
       that the weather station is offline.
       Parameters:
           updateSuccess - a boolean that is True if data request
                           successful, False otherwise
       Returns: nothing
    """
    global failedUpdateCount, deviceOnline

    if updateSuccess:
        failedUpdateCount = 0
        # Set status and send a message to the log if the device
        # previously offline and is now online.
        if not deviceOnline:
            print('%s weather station online' % getTimeStamp())
            deviceOnline = True
        return
    else:
        # The last attempt failed, so update the failed attempts
        # count.
        failedUpdateCount += 1

    if failedUpdateCount == _MAX_FAILED_DATA_REQUESTS:
        # Max number of failed data requests, so set
        # device status to offline.
        failedUpdateCount = 0
        setStatusToOffline()
##end def

def midnightReset(dData):
    """Check the time to see if midnight has just occurred during the last
       device update cycle. If so, then send a reset message to the weather
       station.
       Parameters: none
       Returns: nothing
    """
    global testResetOffsetSec

    # Get the number of seconds that have elapsed since midnight.
    now = time.localtime()
    secondsSinceMidnight = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    
    # Perform a test of the midnight reset feature if requested.
    if testResetOffsetSec > 0:
        secondsSinceMidnight = abs(testResetOffsetSec - secondsSinceMidnight)
        #print('midnight: %f %f' % (testResetOffsetSec, secondsSinceMidnight))

    # If the number of elapsed seconds is smaller than the update interval
    # then midnight has just occurred and the weather station needs to be
    # sent its daily reset.
    if secondsSinceMidnight < dataRequestInterval:
        if verboseMode: # or True:
            print('%s midnight reset' % \
                 (getTimeStamp()))
        ra1.resetDailyRain()
        testResetOffsetSec = 0            
##end def

    ### GRAPH FUNCTIONS ###

def generateDayGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    rrdb.createWeaGraph('1d_tempf', 'tempf', 'degrees Fahrenheit', \
                'Temperature', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_pressure', 'pressure', 'inches Hg', \
                'Barometric PrHessure', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_humidity', 'humidity', 'percent', \
                'Relative Humidity', 'now-1d', 0, 0, 0, True)
    rrdb.createWeaGraph('1d_hourly_rain', 'hourly_rain', 'inches', \
                'Rain Fall', 'now-1d', 0, 0, 0, False)
##end def

def generateLongGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns: nothing
    """
    # 10 day long graphs
    rrdb.createWeaGraph('10d_tempf', 'tempf', 'degrees Fahrenheit', \
                'Temperature', 'end-10days',0, 0, 0, True)
    rrdb.createWeaGraph('10d_pressure', 'pressure', 'inches Hg', \
                'Barometric Pressure', 'end-10days',0, 0, 0, True)
    rrdb.createWeaGraph('10d_humidity', 'humidity', 'percent', \
                'Relative Humidity', 'end-10days', 0, 0, 0, True)
    rrdb.createWeaGraph('10d_hourly_rain', 'hourly_rain', 'inches', \
                'Rain Fall', 'end-10days', 0, 0, 0, False)

    # 3 month long graphs
    rrdb.createWeaGraph('3m_tempf', 'tempf', 'degrees Fahrenheit', \
                'Temperature', 'end-3months',0, 0, 2, True)
    rrdb.createWeaGraph('3m_pressure', 'pressure', 'inches Hg', \
#                'Barometric Pressure', 'end-3months', 29.0, 30.8, 2, True)
                'Barometric Pressure', 'end-3months', 0, 0, 2, True)
    rrdb.createWeaGraph('3m_humidity', 'humidity', 'percent', \
                'Relative Humidity', 'end-3months', 0, 0, 2, True)
    rrdb.createWeaGraph('3m_hourly_rain', 'hourly_rain', 'inches', \
                'Rain Fall', 'end-3months', 0, 0, 0, False)

    # 12 month long graphs
    rrdb.createWeaGraph('12m_tempf', 'tempf', 'degrees Fahrenheit', \
                'Temperature', 'end-12months',0, 0, 0, True)
    rrdb.createWeaGraph('12m_pressure', 'pressure', 'inches Hg', \
#                'Barometric Pressure', 'end-12months', 29.0, 30.8, 1, True)
                'Barometric Pressure', 'end-12months', 0, 0, 0, True)
    rrdb.createWeaGraph('12m_humidity', 'humidity', 'percent', \
                'Relative Humidity', 'end-12months', 0, 0, 0, True)
    rrdb.createWeaGraph('12m_hourly_rain', 'hourly_rain', 'inches', \
                'Rain Fall', 'end-12months', 0, 0, 0, False)
##end def

def getCLarguments():
    """Get command line arguments - there are two possible arguments
          -d turns on debug mode
          -v turns on verbose mode
          -p sets the update poll interval in seconds (default=10)
       Returns: nothing
    """
    global verboseMode, debugMode, dataRequestInterval
    global reportUpdateFails, sourceUrl, sourceServer

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

        # Polling period and url options
        elif sys.argv[index] == '-p':
            dataRequestInterval = float(sys.argv[index + 1])
            index += 1
        elif sys.argv[index] == '-u':
            sourceUrl = sys.argv[index + 1]
            index += 1
        elif sys.argv[index] == '-s':
            sourceServer = sys.argv[index + 1]
            index += 1
        else:
            cmd_name = sys.argv[0].split('/')
            print("Usage: %s [-d|v|r] [-p seconds] [-u source url]" \
                  % cmd_name[-1])
            exit(-1)
        index += 1
    ##end while
    return
##end def

     ### MAIN ROUTINE ###

def main_setup():
    """Executive routine which manages timing and execution of all other
       events.
       Parameters: none
       Returns nothing.
    """
    global tph1, ra1, rrdb, serverMode, sourceUrl, sourceServer, urlopen

    signal.signal(signal.SIGTERM, terminateAgentProcess)
    signal.signal(signal.SIGINT, terminateAgentProcess)

    #testMidnightResetFeature()

    # Log agent process startup time.
    print('%s * starting up weather agent process' % \
                  (getTimeStamp()))

    # Get the command line arguments.
    getCLarguments()

    ## Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print('rrdtool database does not exist\n' \
              'use createWeatherRrd script to ' \
              'create rrdtool database\n')
        exit(1)

    # Define server mode.  The source server receives weather data directly from
    # connected sensors.  A mirror server gets raw data from the upstream source
    # server via http request.
    
    hostName = os.uname()[1]
    if hostName == sourceServer or hostName == _DEFAULT_SOURCE_SERVER:
        serverMode = "source"
    else:
        if sourceUrl == None:
            sourceUrl = _DEFAULT_SOURCE_SERVER_URL
        if sourceUrl.find('http://') < 0:
            sourceUrl = 'http://' + sourceUrl
        serverMode = "mirror"

    if serverMode == "source":
        # Import sensor libraries.
        import ms8607
        import rainfall
        # Instantiate sensor objects.
        #   tph1: temperature, pressure, humidity sensor
        #   ra1: rainfall sensor
        tph1 = ms8607.ms8607(debug=False)
        ra1 = rainfall.rainfall(dataRequestInterval=dataRequestInterval, \
                                  debug=False)
    else:
        from urllib.request import urlopen

    # Define object for calling rrdtool database functions.
    rrdb = rrdbase.rrdbase( _RRD_FILE, _CHARTS_DIRECTORY, _CHART_WIDTH, \
                            _CHART_HEIGHT, verboseMode, debugMode )
## end def

def main_loop():
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

            if serverMode == "source":
                # At midnight set daily rain to zero.
                midnightReset(dData)
                # Send a request for weather data to the weather sensors.
                result = getDataFromSensors(dData)
                # If data successfully received from the sensors, 
                # then convert the data.
                if result:
                    result = convertData(dData)
            else:
                # If we are a mirror server, then use an http request
                # to retrieve data from the source server.
                result = getDataFromSourceServer(dData)

            # If the data successfully converted, then the write the data
            # to the output data file.
            if result:
               result = writeOutputFile(dData)

            # At the rrdtool database update interval write the data to
            # the rrdtool database.
            if result and (currentTime - lastDatabaseUpdateTime >
                           _DATABASE_UPDATE_INTERVAL):   
                lastDatabaseUpdateTime = currentTime

                # 2025-08-17 Added for testing for transients in rain
                # sensor circuit.  Stores 0.0 inches hourly rainfall
                # in rrdtool database. Prevents transients from being
                # stored in database. 
                #dData['hourly_rain'] = '0.0'

                # Update the round robin database with the parsed data.
                result = rrdb.updateDatabase(dData['time'], -1, -1, \
                         dData['tempf'], dData['hourly_rain'], \
                         dData['pressure'], dData['humidity'])

            # Set the station status to online or offline depending on the
            # success or failure of the above operations.
            setStatus(result)
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
        if verboseMode:
            if result:
                print("request successful: %6f sec\n"
                      % elapsedTime)
            else:
                print("request failed: %6f sec\n"
                      % elapsedTime)
        remainingTime = dataRequestInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
##end def

if __name__ == '__main__':
    main_setup()
    main_loop()

##end module
