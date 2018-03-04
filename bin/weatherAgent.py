#!/usr/bin/python -u
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
#   * v14 03 Mar 2018 by J L Owrey; improved online status diagnostic output;
#         improved code readability and comments
#

# set to True in this script is running on a mirror server
_MIRROR_SERVER = False

import urllib2
import os
import sys
import subprocess
import multiprocessing
import time
import json

    ### FILE AND FOLDER LOCATIONS ###

# get the user running this script
_USER = os.environ['USER']
# html document root directory
_DOCROOT_DIRECTORY = '/home/%s/public_html/weather/' % _USER
# location of weather charts used by html documents
_CHARTS_DIRECTORY = _DOCROOT_DIRECTORY + 'dynamic/'
# raw, time stamped data from weather station
_INPUT_DATA_FILE = _DOCROOT_DIRECTORY + 'dynamic/weatherInputData.js'
# data for use by other services and html documents
_OUTPUT_DATA_FILE = _DOCROOT_DIRECTORY + 'dynamic/weatherOutputData.js'
# maintenance signals to be transmitted to the weather station
_MAINTENANCE_FILE = _DOCROOT_DIRECTORY + 'maintsig'
# rrdtool database file
_RRD_FILE = '/home/%s/database/weatherData.rrd' % _USER
# server url used by mirror server to get data from primary server
_PRIMARY_SERVER_URL = '{your primary server weather data url}'

    ### GLOBAL CONSTANTS ###

# maximum number of failed updates from weather station
_MAX_FAILED_UPDATE_COUNT = 2
# web page data item refresh rate (sec)
_DEFAULT_DATA_UPDATE_INTERVAL = 10
# time out for request coming from mirror server
_HTTP_REQUEST_TIMEOUT = 5
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
# correction for elevation above sea level
_BAROMETRIC_PRESSURE_CORRECTION = 0.2266
# conversion of light sensor to percentage value
_LIGHT_SENSOR_FACTOR = 3.0

   ### GLOBAL VARIABLES ###

# turns on or off extensive debugging messages
debugOption = False
# modified by command line argument
dataUpdateInterval = _DEFAULT_DATA_UPDATE_INTERVAL
# used for determining if weather station online or offline
failedUpdateCount = 0
previousUpdateTime = 0
currentUpdateTime = 0
# weather station status online or offline
stationOnline = True

    ### SOFTWARE TEST FUNCTIONS ###

# used for testing midnight reset feature
testResetOffsetSec = -1

    ### HELPER FUNCTIONS ###

def getTimeStamp():
    """
    Sets the error message time stamp to the local system time.
    Parameters: none
    Returns string containing the time stamp.
    """
    return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
##end def

def getEpochSeconds(sTime):
    """Convert the time stamp supplied in the weather data string
       to seconds since 1/1/1970 00:00:00.
       Parameters: 
           sTime - the time stamp to be converted must be formatted
                   as %m/%d/%Y %H:%M:%S
       Returns epoch seconds.
    """
    try:
        t_sTime = time.strptime(sTime, '%m/%d/%Y %H:%M:%S')
    except Exception, exError:
        print '%s getEpochSeconds: %s' % (getTimeStamp(), exError)
        return None
    tSeconds = int(time.mktime(t_sTime))
    return tSeconds
##end def

def setDataItemsToOfflineValues(dData):
    """Set the status of the weather station to "offline" and sends
       blank data to web clients.
       Parameters:
           dData - dictionary object containing weather data
       Returns nothing.
    """
    dData['windspeedmph'] = ''
    dData['winddir'] = 16 
    dData['windgustmph'] = ''
    dData['windgustdir'] = 16
    dData['windspeedmph_avg2m'] = ''
    dData['winddir_avg2m'] = 16
    dData['windgustmph_10m'] = ''
    dData['windgustdir_10m'] = 16
    dData['humidity'] = ''
    dData['tempf'] = ''
    dData['rainin'] = ''
    dData['dailyrainin'] = ''
    dData['pressure'] = ''
    dData['batt_lvl'] = ''
    dData['light_lvl'] = ''
    dData['status'] = 'offline'

    writeOutputDataFile(dData, _OUTPUT_DATA_FILE)
    return
##end def

def setMaintenanceSignal(mSig):
    """Write a message to the weather maintenance file.  This file
       gets read by the submit.php script, which embeds the message into the
       http response back to the weather station device.
       Parameters:
           mSig - a string containing the message to the weather station
       Returns true if successful, false otherwise.
    """
    try:
        fc = open(_MAINTENANCE_FILE, 'w')
        fc.write(mSig)
        fc.close()
    except Exception, exError:
        print '%s setMaintenanceSignal: %s' % (getTimeStamp(), exError)
        return False
    return True
##end def    

    ### PUBLIC FUNCTIONS ###

def readInputDataFile():
    """Retrieves weather station data.  The data is contained in JSON file
    located on the local host. This file gets updated by the submit.php
    script whenever data is received from the weather station.
    Parameters:
        none
    Returns:
        raw weather station data as a string
        None, if unsuccessful
    """
    try:
        fc = open(_INPUT_DATA_FILE, 'r')
        sLine = fc.readline()
        fc.close()
    except Exception, exError:
        print '%s: readInputDataFile: %s' % (getTimeStamp(), exError)
        return None
    else:
        sData = sLine.strip()
        if len(sData) == 0:
            print '%s: input data file empty' % getTimeStamp()
            return None

        return sData
##end def

def getWeatherDataFromPrimaryServer():
    """Send http request to the primary server.  The response
       contains the weather data, formatted as an html document.
    Parameters:
        none
    Returns:
        a string containing the weather data
        None, if unsuccessful.
    """
    try:
        currentTime = time.time()
        connection = urllib2.urlopen(_PRIMARY_SERVER_URL,
                               timeout=_HTTP_REQUEST_TIMEOUT)
        requestTime = time.time() - currentTime
        if debugOption:
            print 'http request: %.4f seconds' % requestTime

        # Format received data into a single string.
        sData = ''
        for line in connection:
            sData += line.strip()
        del connection

    except Exception, exError:
        # If no response is received from the station, then assume that
        # the station is down or unreachable over the network.  In
        # that case set the status of the device to offline.
        if debugOption:
            print '%s http error: %s' % (getTimeStamp(), exError)
        return None
 
    return sData
##end def

def parseInputDataString(sData, dData):
    """Parse the weather data string into its component data items.
       The parsed data is stored in a dictionary object.
       Parameters: 
           sData - string containing the raw weather data
           dData - a dictionary object to contain the parsed weather data
       Returns: True, if successful  
    """
    global currentUpdateTime

    try:
        # Parse JSON formatted data into a temporary dictionary object
        # which will contain the time stamp supplied by the submit.php
        # script and the raw weather data contained in a single string.
        dTmp = json.loads(sData)[0]

        # Extract the time stamp inserted by the submit.php script.
        dData['date'] = dTmp['date'].encode('ascii', 'ignore')

        # Extract the weather data string and place in a temporary
        # list object.
        sTmp = dTmp['weather'].encode('ascii', 'ignore')
        lsTmp = sTmp.split(',')
 
    # Trap any errors that might result from corrupted data.
    except Exception, exError:
        print '%s parse failed: %s' % (getTimeStamp(), exError)
        return False

    # Parse all the elements in lsTmp and load them into the global dData
    # dictionary object.
    for item in lsTmp:
        if '=' in item:
            dData[item.split('=')[0]] = item.split('=')[1]

    if len(dData) != 16:
        print '%s parse failed: corrupted data string' % \
               (getTimeStamp())
        return False;

    currentUpdateTime = getEpochSeconds(dData['date'])
    return True
##end def

def convertData(dData):
    """Convert individual weather data items as necessary.  Also
       format data items for use by html documents.  The keys
       created below are specific to the Weather Underground service.
       Parameters:
           dData - a dictionary object containing the data items to be
                   converted
       Returns true if successful, false otherwise.
    """
    try:
        # Convert pressure from pascals to inches Hg.
        pressure = dData['p']
        pressureBar = float(pressure) * _PASCAL_CONVERSION_FACTOR + \
                          _BAROMETRIC_PRESSURE_CORRECTION
        dData['p'] = '%.2f' % pressureBar # format for web page
 
        # Convert ambient light level to percent
        light = dData['l']
        lightAdj = int(100.0 * float(light) / _LIGHT_SENSOR_FACTOR)
        if lightAdj > 100:
            lightAdj = 100
        dData['l'] = '%d' % lightAdj 
            
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
    except Exception, exError:
        print '%s convertData: %s' % (getTimeStamp(), exError)
        print readInputDataFile()
        return False

    # Bounds checking - in some instances an invalid value
    # indicates a weather station fault requires the
    # weather station to be rebooted.

    sensorValue = float(dData['pressure'])
    if sensorValue < 25.0 or sensorValue > 35.0:
        print '%s invalid pressure: %.4e - discarding' % \
            (getTimeStamp(), sensorValue)
        return False

    sensorValue = float(dData['tempf'])
    if sensorValue < -100.0:
        result = setMaintenanceSignal('!r\n')
        print '%s invalid temperature: %.4e - sending reset signal' % \
            (getTimeStamp(), sensorValue)
        return False

    sensorValue = float(dData['humidity'])
    if sensorValue > 105.0 or sensorValue < 0.0:
        result = setMaintenanceSignal('!r\n')
        print '%s invalid humidity: %.4e - sending reset signal' % \
            (getTimeStamp(), sensorValue)
        return False

    return True
##end def

def checkOnlineStatus(dData):
    """Check to see if the timestamp supplied by the submit.php script is
       updating at the weather station update rate.  If the timestamp
       is not getting updated, then the submit.php script is not receiving
       http requests from the weather station.  In this case assume that
       the weather station is down or not available via the network.
       Parameters: 
           currentTime - system time of current web data update cycle
           dData - dictionary object containing parsed weather data
       Returns true if successful, false otherwise.
    """
    global previousUpdateTime, stationOnline, failedUpdateCount
    
    # Compare with the time stamp from the previous update.
    # If they are equal for more than a specified amount of time, it means
    # that the weather station data has not been updated, and that the
    # weather station is offline or unreachable.
    if (currentUpdateTime == previousUpdateTime):
        if debugOption or True:
            print '%s weather update failed' % getTimeStamp()

        # Set status to offline if a specified number of intervals have
        # elapsed without new data received from the weather station
        if failedUpdateCount >= _MAX_FAILED_UPDATE_COUNT:
            # Set status and send a message to the log if the station was
            # previously online and is now determined to be offline.
            if stationOnline:
                print '%s weather station offline' % getTimeStamp()
                stationOnline = False
                setDataItemsToOfflineValues(dData)
        else:
            failedUpdateCount += 1
        return False
    else:
        if debugOption:
            print 'weather update received'

        # New data received so set status condition to online.
        dData['status'] = 'online'
        previousUpdateTime = currentUpdateTime
        failedUpdateCount = 0

        # Set status and send a message to the log if the station was
        # previously offline and is now online.
        if not stationOnline:
            print '%s weather station online' % getTimeStamp()
            stationOnline = True
        return True
    ##end if
##end def

def writeOutputDataFile(dData, sOutputDataFile):
    """Writes to a file a formatted string containing the weather data.
       The file is written to the document dynamic data folder for use
       by html documents.
       Parameters: 
           dData - dictionary object containing weather data
       Returns true if successful, false otherwise
    """
    # Set date item to current date and time.
    dData['date'] = getTimeStamp()

    # Format the weather data as string using java script object notation.
    sData = '[{'
    for key in dData:
        sData += '\"%s\":\"%s\",' % (key, dData[key])
    sData = sData[:-1] + '}]\n'

    # Write the string to the output data file for use by html documents.
    try:
        fc = open(sOutputDataFile, 'w')
        fc.write(sData)
        fc.close()
    except Exception, exError:
        print '%s writeOutputDataFile: %s' % (getTimeStamp(), exError)
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
        if debugOption:
            print '%s sending midnight reset signal' % \
                  (getTimeStamp())
        result = setMaintenanceSignal('!r\n')
        time.sleep(30)
    return
##end def

    ### DATABASE FUNCTIONS ###

def updateDatabase(dData):
    """Updates the rrdtool round robin database with data supplied in
       the weather data string.
       Parameters:
           dData - dictionary object containing items to be written to the
                   rrdtool database
       Returns true if successful, false otherwise.
    """
   # Get the data items to be stored in rrdtool database, and
   # convert string items to floating point numbers as necessary.
    try:
        time = getEpochSeconds(dData['date'])
        windspeedmph = float(dData['windspeedmph_avg2m'])
        winddir = float(dData['winddir_avg2m']) * 22.5
        tempf = float(dData['tempf'])
        rainin = float(dData['rainin'])
        pressure = float(dData['pressure'])
        humidity = float(dData['humidity'])
    # Trap any data conversion errors.
    except Exception, exError:
        print '%s updateDatabase error: %s' % (getTimeStamp(), exError)
        return False

    # Format the rrdtool update command.
    strCmd = 'rrdtool update %s %s:%s:%s:%s:%s:%s:%s' % \
                  (_RRD_FILE, time, windspeedmph, winddir, tempf, \
                   rainin, pressure, humidity)
    if debugOption:
        #print '%s' % strCmd # DEBUG
        pass

    # Run the formatted command as a subprocess.
    try:
        subprocess.check_output(strCmd, stderr=subprocess.STDOUT, \
                                shell=True)
    except subprocess.CalledProcessError, exError:
        print '%s rrdtool update failed: %s' % \
              (getTimeStamp(), exError.output)
        return False
    else:
        if debugOption:
            print 'database update successful'
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
       Returns true if successful, false otherwise.
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
     
    if debugOption:
        # print '%s\n' % strCmd # DEBUG
        pass
    
    # Run the formatted rrdtool command as a subprocess.
    try:
        result = subprocess.check_output(strCmd, \
                     stderr=subprocess.STDOUT,   \
                     shell=True)
    except subprocess.CalledProcessError, exError:
        print 'rrdtool graph failed: %s' % (exError.output)
        return False

    if debugOption:
        print 'rrdtool graph: %s' % result
    return True
##end def

def generateDayGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns nothing.
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
       Returns nothing.
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
          -t sets the update poll interval in seconds (default=10)
       Returns nothing.
    """
    global debugOption, weatherUpdateInterval

    index = 1
    while index < len(sys.argv):
        if sys.argv[index] == '-d':
            debugOption = True
        elif sys.argv[index] == '-t':
            try:
                tempVal = float(sys.argv[index + 1])
            except:
                print 'invalid update period'
                exit(-1)
            dataUpdateInterval = tempVal
            index += 1
        else:
            cmd_name = sys.argv[0].split('/')
            print 'Usage: %s {-d} {-t}' % cmd_name[-1]
            exit(-1)
        index += 1
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
    testResetOffsetSec = 60 + now.tm_hour * 3600 + now.tm_min * 60 + \
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

    # last time the data file to the web server updated
    lastCheckForUpdateTime = -1
    # last time day charts were generated
    lastDayChartUpdateTime = -1
    # last time long term charts were generated
    lastLongChartUpdateTime = -1
    # last time the rrdtool database updated
    lastDatabaseUpdateTime = -1

    # Get the command line arguments.
    getCLarguments()

    ## Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print 'rrdtool database does not exist\n' \
              'use createWeatherRrd script to' \
              ' create rrdtool database\n'
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
 
            # Get weather station data from the input data file.
            if _MIRROR_SERVER:
                sData = getWeatherDataFromPrimaryServer()
            else:
                # At midnight send the reset signal to the weather station.
                checkForMidnight()
 
                # Read the input data file from the submit.php script which
                # processes data from the weather station.
                sData = readInputDataFile() 
            if sData == None:
                result = False

            # Upon successfully getting the data, parse the data.
            if result:
                 result = parseInputDataString(sData, dData)

            # Verify that the weather station is still online.
            status = checkOnlineStatus(dData)
            result = result and status

            # If the station is online and the data successfully parsed, 
            # then convert the data.
            if result:
                result = convertData(dData)

            # If the data successfully converted, then the write the data
            # to the output data file.
            if result:
               writeOutputDataFile(dData, _OUTPUT_DATA_FILE)

               # At the rrdtool database update interval write the data to
               # the rrdtool database.
               if currentTime - lastDatabaseUpdateTime > \
                       _DATABASE_UPDATE_INTERVAL:   
                   lastDatabaseUpdateTime = currentTime
                   # Update the round robin database with the parsed data.
                   result = updateDatabase(dData)

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
        if debugOption:
            print 'processing time: %6f sec\n' % elapsedTime
        remainingTime = dataUpdateInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
    return
## end def

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print '\nInterrupted'
        if os.path.exists(_OUTPUT_DATA_FILE):
            os.remove(_OUTPUT_DATA_FILE)

