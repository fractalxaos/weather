#!/usr/bin/python -u
## The -u option turns off block buffering of python output. This assures
## that error messages get printed to the log file as they happen.
#  
# Module: weatherAgent.py
#
# Description: This module acts as an agent between the weather station
# device and the Internet web server.  The agent works by periodically
# sending an http request to the weather station.  The weather station
# responds with a text string that the agent then parses and stores the
# the data in a rrdtool database.  The agent creates a JSON file with
# updated weather data.  Javascript in HTML documents may request this
# JSON file to display data on web pages.  Finally the agent periodically uses
# rrdtool to generate plots of recorded weather data.
# 
# In summary, the agent:
#     - convert units of various weather data items
#     - verify the time stamp to determine device on or off line status
#     - update the weather maintenance signal file (initiates a
#       daily maintenance reset of the weather station)
#     - update a round robin (rrdtool) database with the weather data
#     - periodically generate graphic charts for display in html documents
#     - forward the weather data to other services, such as Weather
#       Underground
#     - write the processed weather data to a JSON file for use by html
#       documents
#       
# Copyright 2015 Jeff Owrey
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
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see http://www.gnu.org/license.
#
# Revision History
#   * v10 15 Sep 2015 by J L Owrey; first release
#   * v12 08 Jan 2016 by J L Owrey; modified to use wind direction expressed
#         as compass points, improved device status checking
#   * v13 21 Sept 2016 by J L Owrey; added chart generation of longer time
#         time period charts
#   * v14 27 Oct 2016 by J L Owrey; added feature to define primary server
#         and to detect, based on user id, the role (primary or mirror)
#         of the server
#   * v15 01 May 2017 by J L Owrey; added data validation rules to convertData
#         function to handle occasionally data glitches from weather station
#

import os
import urllib2
import sys   
import subprocess
import multiprocessing
import time

_USER = os.environ['USER']

   ### DEFAULT WEATHER STATION URL ###

_DEFAULT_WEATHER_STATION_URL = "your server url"

    ### FILE AND FOLDER LOCATIONS ###

# temporary files for use by html documents
_TMP_DIRECTORY = "/tmp/weather/"
# rrdtool database file stores radiation data
_RRD_FILE = "/home/%s/database/weatherData.rrd" % _USER
# data for use by html documents
_OUTPUT_DATA_FILE = \
    "/home/%s/public_html/weather/dynamic/weatherData.js" % _USER

    ### GLOBAL CONSTANTS ###
# weather station PIN
_STATION_PIN = '1234'
# web page data item refresh rate (sec)
_DEFAULT_DATA_REQUEST_INTERVAL = 10
# generation rate of day charts (sec)
_DAY_CHART_UPDATE_INTERVAL = 300
# generation rate of long period charts (sec)
_LONG_CHART_UPDATE_INTERVAL = 3600
# rrdtool database update rate (sec)
_DATABASE_UPDATE_INTERVAL = 60
# number seconds to wait for a response to HTTP request
_HTTP_REQUEST_TIMEOUT = 2
# max number of failed data requests allowed
_MAX_WEATHER_STATION_OFFLINE_COUNT = 5
# width in pixels of generated charts
_CHART_WIDTH = 600
# height in pixels of generated charts
_CHART_HEIGHT = 150

   ### CONVERSION FACTORS ###

# inches Hg per Pascal
_PASCAL_CONVERSION_FACTOR = 0.00029530099194
# correction for elevation above sea level
_BAROMETRIC_PRESSURE_CORRECTION = 0.2266
# correction for battery voltage offset at test point
_BATTERY_VOLTAGE_OFFSET = 0.0
 # conversion of light sensor to percentage value
_LIGHT_SENSOR_FACTOR = 2.9

   ### GLOBAL VARIABLES ###

# turns on or off debugging output
debugOption = False
# turns on logging of http timeouts
displayHttpErrors = False
# weather station status online or offline
weatherStationOnline = True
# number of unsuccessful data requests
weatherStationOfflineCount = 0
# weather station maintenance command
maintenanceCommand = ""
# web update frequency
dataRequestInterval = _DEFAULT_DATA_REQUEST_INTERVAL
# weather station network address
weatherStationUrl = _DEFAULT_WEATHER_STATION_URL

    ### SOFTWARE TEST FUNCTIONS ###

# Simple routine for testing station midnight reset feature by sending
# a reset signal 60 seconds after this program starts.
now = time.localtime()
testResetOffsetSec = 60 + now.tm_hour * 3600 + now.tm_min * 60 + \
                     now.tm_sec

    ### HELPER FUNCTIONS ###

def getTimeStamp():
    """
    Sets the error message time stamp to the local system time.
    Parameters: none
    Returns string containing the time stamp.
    """
    return time.strftime( "%m/%d/%Y %H:%M:%S", time.localtime() )
##end def

def getEpochSeconds(sTime):
    """Convert the time stamp supplied in the weather data string
       to seconds since 1/1/1970 00:00:00.
       Parameters: 
           sTime - the time stamp to be converted
       Returns the converted time stamp as a string.
    """
    try:
        t_sTime = time.strptime(sTime, "%m/%d/%Y %H:%M:%S")
    except Exception, exError:
        print "%s getEpochSeconds: %s" % (getTimeStamp(), exError)
        return None
    tSeconds = int(time.mktime(t_sTime))
    return tSeconds
##end def

def setOfflineStatus(dData):
    """Set the status of the weather station to "offline" and send
       blank data to web clients.
       Parameters:
           dData - dictionary object containing weather data
       Returns nothing.
    """
    global weatherStationOnline, weatherStationOfflineCount

    weatherStationOfflineCount += 1

    if weatherStationOfflineCount < _MAX_WEATHER_STATION_OFFLINE_COUNT:
        return
    
    # If the weather station was previously online, then send a message
    # that we are now offline.
    if weatherStationOnline:
        print "%s: weather station offline" % getTimeStamp()
        if os.path.exists(_DATA_FORWARDING_FILE):
            os.remove(_DATA_FORWARDING_FILE)
        weatherStationOnline = False

    for key in dData:
        dData[key] = ''

    dData['winddir'] = 17 
    dData['windgustdir'] = 17
    dData['winddir_avg2m'] = 17
    dData['windgustdir_10m'] = 17
    dData['date'] = getTimeStamp()
    dData['status'] = 'offline'

    writeOutputDataFile(dData)

    return
##end def

    ### PUBLIC FUNCTIONS ###

def getWeatherDataFromRemoteServer():
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
    global weatherStationOnline, weatherStationOfflineCount, \
        maintenanceCommand

    try:
        currentTime = time.time()
        conn = urllib2.urlopen(weatherStationUrl + maintenanceCommand,
                               timeout=_HTTP_REQUEST_TIMEOUT)
        requestTime = time.time() - currentTime
        if debugOption:
            print "http request: %.4f seconds" % requestTime

        # Format received data into a single string.
        content = ""
        for line in conn:
            content += line.strip()
        del conn

    except Exception, exError:
        # If no response is received from the station, then assume that
        # the station is down or unreachable over the network.  In
        # that case set the status of the device to offline.
        if displayHttpErrors or debugOption:
            print "%s http error: %s" % (getTimeStamp(), exError)
        return None

    if debugOption:
        #print content
        pass
    
    # If the weather station was previously offline, then send a message
    # that we are now online.
    if not weatherStationOnline:
        print "%s weather station online" % getTimeStamp()
        weatherStationOnline = True
    weatherStationOfflineCount = 0

    # If reset command was sent to weather station then allow extra time
    # for the station to reset and re-acquire the wifi access point.
    if maintenanceCommand[0:2] == '/r':
        maintenanceCommand = ''
        time.sleep(20)
        return None

    return content
##end def

def parseDataString(sData, dData):
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
    except Exception, exError:
        print "%s parseDataString: %s" % (getTimeStamp(), exError)
        return False

    # Load the parsed data into a dictionary object for easy access.
    for item in lsTmp:
        if "=" in item:
            dData[item.split('=')[0]] = item.split('=')[1]
    # Add date and status to dictionary object
    dData['status'] = 'online'
    dData['date'] = getTimeStamp()

    if len(dData) != 17:
        #print "%s parse failed: corrupted data string" % (getTimeStamp())
        return False;

    return True
##end def

def convertData(dData):
    """Convert individual weather data items as necessary.  Also
       format data items for use by other weather services.  The keys
       created below are specific to the Weather Underground service.
       Parameters:
           dData - a dictionary object containing the data items
                   to be converted
       Returns true if successful, false otherwise.
    """
    global maintenanceCommand

    try:
        # Convert pressure from pascals to inches Hg.
        pressure = dData['p']
        pressureBar = float(pressure) * _PASCAL_CONVERSION_FACTOR + \
                          _BAROMETRIC_PRESSURE_CORRECTION
        dData['p'] = "%.2f" % pressureBar # format for web page
 
        # Adjust battery voltage as measured on the input side of Arduino
        # 5 volt regulator to voltage as seen on output side of regulator.
        battery = dData['b']
        batteryAdj = float(battery) + _BATTERY_VOLTAGE_OFFSET
        dData['b'] = "%.2f" % batteryAdj # format for web page

        # Convert ambient light level to percent
        light = dData['l']
        lightAdj = int(100.0 * float(light) / _LIGHT_SENSOR_FACTOR)
        if lightAdj > 100:
            lightAdj = 100
        dData['l'] = "%d" % lightAdj 
            
        # Replace key names with their long form name.
        dData['windspeedmph'] = dData.pop('ws')
        dData['winddir'] = dData.pop('wd') 
        dData['windgustmph'] = dData.pop('wgs')
        dData['windgustdir'] = dData.pop('wgd')
        dData['windspeedmph_avg2m'] = dData.pop('ws2')
        dData['winddir_avg2m'] = dData.pop('wd2')
        dData['windgustmph_10m'] = dData.pop('wgs10')
        dData['windgustdir_10m'] = dData.pop('wgd10')
        dData['humidity'] = dData.pop('h')
        dData['tempf'] = dData.pop('t')
        dData['rainin'] = dData.pop('r')
        dData['dailyrainin'] = dData.pop('dr')
        dData['pressure'] = dData.pop('p')
        dData['batt_lvl'] = dData.pop('b')
        dData['light_lvl'] = dData.pop('l')

    # Trap any data conversion errors.
    except Exception, exError:
        print "%s convertData: %s" % (getTimeStamp(), exError)
        return False

    # Bounds checking

    sensorValue = float(dData['pressure'])
    if sensorValue < 28.0 or sensorValue > 31.0:
        print "%s invalid pressure: %.4e - discarding" % \
            (getTimeStamp(), sensorValue)
        return False

    sensorValue = float(dData['tempf'])
    if sensorValue < -100.0:
        maintenanceCommand = "/r"
        print "%s invalid temperature: %.4e - sending reset command" % \
            (getTimeStamp(), sensorValue)
        return False

    sensorValue = float(dData['humidity'])
    if sensorValue > 100.0 or sensorValue < 0.0:
        maintenanceCommand = "/r"
        print "%s invalid humidity: %.4e - sending reset command" % \
            (getTimeStamp(), sensorValue)
        return False

    return True
##end def

def writeOutputDataFile(dData):
    """Writes to a file a formatted string containing the weather data.
       The file is written to the /www/wwwdata folder for access by html
       documents.
       Parameters: 
           dData - dictionary object containing weather data
       Returns true if successful, false otherwise
    """
    # Set date item to current date and time.
    #dData['date'] = getTimeStamp()

    # Format the weather data as string using java script object notation.
    sData = '[{'
    for key in dData:
        sData += "\"%s\":\"%s\"," % (key, dData[key])
    sData = sData[:-1] + '}]'

    # Write the string to the output data file for use by html documents.
    try:
        fc = open(_OUTPUT_DATA_FILE, "w")
        fc.write(sData)
        fc.close()
    except Exception, exError:
        print "%s writeOutputDataFile: %s" % (getTimeStamp(), exError)
        return False

    return True
##end def

def checkForMidnight():
    """Check the time to see if midnight has just occurred during the last
       device update cycle. If so, then send a reset command to the weather
       station.
       Parameters: none
       Returns true if successful, false otherwise.
    """
    global maintenanceCommand

    # Get the number of seconds that have elapsed since midnight.
    now = time.localtime()
    secondsSinceMidnight = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    
    # Uncomment line below to test the midnight reset feature.
    # secondsSinceMidnight = abs(testResetOffsetSec - secondsSinceMidnight)

    # If the number of elapsed seconds is smaller than the update interval
    # then midnight has just occurred and the weather station needs to be
    # sent its daily reset.
    if secondsSinceMidnight < dataRequestInterval:
        if debugOption or 0:
            print "%s sending midnight reset to weather station" % \
                (getTimeStamp())
        maintenanceCommand = "/r/" + _STATION_PIN
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
        print "%s updateDatabase: %s" % (getTimeStamp(), exError)
        return False

    # Format the rrdtool update command.
    strCmd = "rrdtool update %s %s:%s:%s:%s:%s:%s:%s" % \
                  (_RRD_FILE, time, windspeedmph, winddir, tempf, \
                   rainin, pressure, humidity)
    if debugOption:
        print "%s" % strCmd # DEBUG

    # Run the formatted command as a subprocess.
    try:
        subprocess.check_output(strCmd, stderr=subprocess.STDOUT, \
                     shell=True)
    except subprocess.CalledProcessError, exError:
        print "%s rrdtool update failed: %s" % (getTimeStamp(), exError.output)
        return False 
    return True
##end def

def createAutoGraph(fileName, dataItem, gLabel, gTitle, gStart,
                    lower, upper, addTrend, autoScale):
    """Uses rrdtool to create a graph of specified weather data item.  Graphs
       are for display in html documents.
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
    gPath = _TMP_DIRECTORY + "/" + fileName + ".png"

    # Format the rrdtool graph command.

    # Set chart start time, height, and width.
    strCmd = "rrdtool graph %s -a PNG -s %s -e 'now' -w %s -h %s " \
             % (gPath, gStart, _CHART_WIDTH, _CHART_HEIGHT)
   
    # Set the range and scaling of the chart y-axis.
    if lower < upper:
        strCmd  +=  "-l %s -u %s -r " % (lower, upper)
    elif autoScale:
        strCmd += "-A "
    strCmd += "-Y "

    # Set the chart ordinate label and chart title. 
    strCmd += "-v %s -t %s " % (gLabel, gTitle)

    # Show the data, or a moving average trend line, or both.
    strCmd += "DEF:dSeries=%s:%s:AVERAGE " % (_RRD_FILE, dataItem)
    if addTrend == 0:
        strCmd += "LINE2:dSeries#0400ff "
    elif addTrend == 1:
        strCmd += "CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#0400ff "
    elif addTrend == 2:
        strCmd += "LINE1:dSeries#0400ff "
        strCmd += "CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#0400ff "
    
    # if wind plot show color coded wind direction
    if dataItem == 'windspeedmph':
        strCmd += "DEF:wDir=%s:winddir:AVERAGE " % (_RRD_FILE)
        strCmd += "VDEF:wMax=dSeries,MAXIMUM "
        strCmd += "CDEF:wMaxScaled=dSeries,0,*,wMax,+,-0.15,* "
        strCmd += "CDEF:ndir=wDir,337.5,GE,wDir,22.5,LE,+,wMaxScaled,0,IF "
        strCmd += "CDEF:nedir=wDir,22.5,GT,wDir,67.5,LT,*,wMaxScaled,0,IF "
        strCmd += "CDEF:edir=wDir,67.5,GE,wDir,112.5,LE,*,wMaxScaled,0,IF "
        strCmd += "CDEF:sedir=wDir,112.5,GT,wDir,157.5,LT,*,wMaxScaled,0,IF "
        strCmd += "CDEF:sdir=wDir,157.5,GE,wDir,202.5,LE,*,wMaxScaled,0,IF "
        strCmd += "CDEF:swdir=wDir,202.5,GT,wDir,247.5,LT,*,wMaxScaled,0,IF "
        strCmd += "CDEF:wdir=wDir,247.5,GE,wDir,292.5,LE,*,wMaxScaled,0,IF "
        strCmd += "CDEF:nwdir=wDir,292.5,GT,wDir,337.5,LT,*,wMaxScaled,0,IF "
  
        strCmd += "AREA:ndir#0000FF:N "    # Blue
        strCmd += "AREA:nedir#1E90FF:NE "  # DodgerBlue
        strCmd += "AREA:edir#00FFFF:E "    # Cyan
        strCmd += "AREA:sedir#00FF00:SE "  # Lime
        strCmd += "AREA:sdir#FFFF00:S "    # Yellow
        strCmd += "AREA:swdir#FF8C00:SW "  # DarkOrange 
        strCmd += "AREA:wdir#FF0000:W "    # Red
        strCmd += "AREA:nwdir#FF00FF:NW "  # Magenta
     
    if debugOption:
        print "%s\n" % strCmd # DEBUG
    
    # Run the formatted rrdtool command as a subprocess.
    try:
        result = subprocess.check_output(strCmd, \
                     stderr=subprocess.STDOUT,   \
                     shell=True)
    except subprocess.CalledProcessError, exError:
        print "rrdtool graph failed: %s" % (exError.output)
        return False

    if debugOption:
        print "rrdtool graph: %s" % result
    return True
##end def

def generateDayGraphs():
    """Generate graphs for html documents. Calls createGraph for each graph
       that needs to be created.
       Parameters: none
       Returns nothing.
    """
    createAutoGraph('1d_windspeedmph', 'windspeedmph', 'miles\ per\ hour', \
                'Average\ Wind\ Speed', 'now-1d', 0, 0, 0, True)
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
                'Average\ Wind\ Speed', 'end-10days', 0, 0, 0, True)
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
                'Average\ Wind\ Speed', 'end-3months', 0, 0, 2, True)
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
                'Average\ Wind\ Speed', 'end-12months', 0, 0, 1, True)
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
    """Get command line arguments.  There are three possible arguments
          -d turns on debug mode
          -t sets the radiation device query interval
          -u sets the url of the radiation monitoring device
       Returns nothing.
    """
    global debugOption, dataRequestInterval, weatherStationUrl, \
           displayHttpErrors

    index = 1
    while index < len(sys.argv):
        if sys.argv[index] == '-d':
            debugOption = True
        elif sys.argv[index] == '-t':
            try:
                dataRequestInterval = abs(int(sys.argv[index + 1]))
            except:
                print "invalid polling period"
                exit(-1)
            index += 1
        elif sys.argv[index] == '-u':
            weatherStationUrl = sys.argv[index + 1]
            index += 1
        elif sys.argv[index] == '-o':
            displayHttpErrors = True
        else:
            cmd_name = sys.argv[0].split('/')
            print "Usage: %s [-d] [-t seconds] [-u url]" % cmd_name[-1]
            exit(-1)
        index += 1
##end def

     ### MAIN ROUTINE ###

def main():
    """Executive routine which manages timing and execution of all other events.
       Parameters: none
       Returns nothing.
    """
    lastDataRequestTime = -1 # last time the data file to the web server updated
    lastDayChartUpdateTime = -1 # last time day charts were generated
    lastLongChartUpdateTime = -1 # last time long term charts were generated
    lastDatabaseUpdateTime = -1 # last time the rrdtool database updated
    dData = {} # dictionary object for temporary data storage

    # Get the command line arguments.
    getCLarguments()

    ## Create www data folder if it does not exist
    if not os.path.isdir(_TMP_DIRECTORY):
        os.makedirs(_TMP_DIRECTORY)

    ## Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print "cannot find rrdtool database\nuse createWeatherRrd script to" \
              " create rrdtool database\n"
        exit(1)

    ## main loop
    while True:

        currentTime = time.time() # get current time in seconds

        # Every web update interval request data from the weather
        # station and process the received data.
        if currentTime - lastDataRequestTime > dataRequestInterval:
            lastDataRequestTime = currentTime
            
            # At midnight send the reset command to the weather station.
            checkForMidnight();
            result = True

            # Get weather station data from the input data file.
            dData = {}
            sData = getWeatherData()
            if sData == None:
                setOfflineStatus(dData)
                result = False

            # Upon successfully getting the data, parse the data.
            if result:
                result = parseDataString(sData, dData)

            # Upon successfully parsing the data, convert the data.
            if result:
                result = convertData(dData)

            # Upon successfully converting the data, write data to JSON
            # formatted output data file.
            if result:
               writeOutputDataFile(dData)
      
               # At the rrdtool database update interval update the rrdtool
               # database.
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
            print "processing time: %.4f seconds\n" % elapsedTime
        remainingTime = dataRequestInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
    return
## end def

if __name__ == '__main__':
    main()

