#!/usr/bin/python3 -u
#
# Module: rrdbase.py
#
# Description: This module acts as an interface between the agent module
# the rrdtool command line app.  Interface functions provide for updating
# the rrdtool database and for creating charts. This module acts as a
# library module that can be imported into and called from other
# Python programs.
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

import subprocess
import time

class rrdbase:

    def __init__(self, rrdFile, chartsDirectory, chartWidth, \
                 chartHeight, verboseMode, debugMode):
        """Initialize instance variables that remain constant throughout
           the life of this object instance.  These items are set by the
           calling module.
           Parameters:
             rrdFile - the path to the rrdtool database file
             chartsDirectory - the path to the folder to contain charts
             chartWidth - the width of charts in pixels
             chartHeight - the height of charts  in pixels
             verboseMode - verbose output
             debugMode - full debug output
           Returns: nothing
        """
        self.rrdFile = rrdFile
        self.chartsDirectory = chartsDirectory
        self.chartWidth = chartWidth
        self.chartHeight = chartHeight
        self.verboseMode = verboseMode
        self.debugMode = debugMode
    ## end def

    def getTimeStamp():
        """Sets the error message time stamp to the local system time.
           Parameters: none
           Returns: string containing the time stamp
        """
        return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
    ## end def

    def getEpochSeconds(sTime):
        """Converts the time stamp supplied in the weather data string
           to seconds since 1/1/1970 00:00:00.
           Parameters: 
               sTime - the time stamp to be converted must be formatted
                       as %m/%d/%Y %H:%M:%S
           Returns: epoch seconds
        """
        try:
            t_sTime = time.strptime(sTime, '%m/%d/%Y %H:%M:%S')
        except Exception as exError:
            print('%s getEpochSeconds: %s' % \
                  (rrdbase.getTimeStamp(), exError))
            return None
        tSeconds = int(time.mktime(t_sTime))
        return tSeconds
    ## end def

    def updateDatabase(self, *tData):
        """Updates the rrdtool round robin database with data supplied in
           the weather data string.
           Parameters:
               tData - a tuple object containing the data items to be written
                       to the rrdtool database
           Returns: True if successful, False otherwise
        """
        # Get the time stamp supplied with the data.  This must always be
        # the first element of the tuple argument passed to this function.
        tData = list(tData)
        date = tData.pop(0)
        # Convert the time stamp to unix epoch seconds.
        try:
            time = rrdbase.getEpochSeconds(date)
        # Trap any data conversion errors.
        except Exception as exError:
            print('%s updateDatabase error: %s' % \
                  (rrdbase.getTimeStamp(), exError))
            return False

        # Create the rrdtool command for updating the rrdtool database.  Add a
        # '%s' format specifier for each data item remaining in tData. 
        # Note that this is the list remaining after the
        # first item (the date) has been removed by the above code.
        strFmt = 'rrdtool update %s %s' + ':%s' * len(tData)
        strCmd = strFmt % ((self.rrdFile, time,) + tuple(tData))

        if self.debugMode:
            print('%s' % strCmd) # DEBUG

        # Run the formatted command as a subprocess.
        try:
            subprocess.check_output(strCmd, stderr=subprocess.STDOUT, \
                                    shell=True)
        except subprocess.CalledProcessError as exError:
            print('%s rrdtool update failed: %s' % \
                  (rrdbase.getTimeStamp(), exError.output.decode('utf-8')))
            return False

        if self.verboseMode and not self.debugMode:
            print('database update successful')

        return True
    ## end def

    def createWeaGraph(self, fileName, dataItem, gLabel, gTitle, gStart,
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
        gPath = self.chartsDirectory + fileName + '.png'

        # Format the rrdtool graph command.

        # Set chart start time, height, and width.
        strCmd = 'rrdtool graph %s -a PNG -s %s -e \'now\' -w %s -h %s ' \
                 % (gPath, gStart, self.chartWidth, self.chartHeight)
       
        # Set the range and scaling of the chart y-axis.
        if lower < upper:
            strCmd  +=  '-l %s -u %s -r ' % (lower, upper)
        elif autoScale:
            strCmd += '-A '
        strCmd += '-Y '

        # Set the chart ordinate label and chart title. 
        strCmd += '-v %s -t %s ' % (gLabel, gTitle)

        # Show the data, or a moving average trend line, or both.
        strCmd += 'DEF:dSeries=%s:%s:AVERAGE ' % (self.rrdFile, dataItem)
        if addTrend == 0:
            strCmd += 'LINE1:dSeries#0400ff '
        elif addTrend == 1:
            strCmd += 'CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#006600 '
        elif addTrend == 2:
            strCmd += 'LINE1:dSeries#0400ff '
            strCmd += 'CDEF:smoothed=dSeries,86400,TREND LINE2:smoothed#006600 '

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
        
        if self.debugMode:
            print('%s' % strCmd) # DEBUG
        
        # Run the formatted rrdtool command as a subprocess.
        try:
            result = subprocess.check_output(strCmd, \
                         stderr=subprocess.STDOUT,   \
                         shell=True)
        except subprocess.CalledProcessError as exError:
            print('rrdtool graph failed: %s' % (exError.output.decode('utf-8')))
            return False

        if self.verboseMode:
            print('rrdtool graph: %s' % result.decode('utf-8')) #, end='')

        return True
    ## end def

    def createAutoGraph(self, fileName, dataItem, gLabel, gTitle, gStart,
                    lower, upper, addTrend, autoScale):
        """Uses rrdtool to create a graph of specified radmon data item.
           Parameters:
               fileName - name of file containing the graph
               dataItem - data item to be graphed
               gLabel - string containing a graph label for the data item
               gTitle - string containing a title for the graph
               gStart - beginning time of the graphed data
               lower - lower bound for graph ordinate #NOT USED
               upper - upper bound for graph ordinate #NOT USED
               addTrend - 0, show only graph data
                          1, show only a trend line
                          2, show a trend line and the graph data
               autoScale - if True, then use vertical axis auto scaling
                   (lower and upper parameters are ignored), otherwise use
                   lower and upper parameters to set vertical axis scale
           Returns: True if successful, False otherwise
        """
        gPath = self.chartsDirectory + fileName + ".png"
        trendWindow = { 'end-1day': 7200,
                        'end-4weeks': 172800,
                        'end-12months': 604800 }
     
        # Format the rrdtool graph command.

        # Set chart start time, height, and width.
        strCmd = "rrdtool graph %s -a PNG -s %s -e now -w %s -h %s " \
                 % (gPath, gStart, self.chartWidth, self.chartHeight)
       
        # Set the range and scaling of the chart y-axis.
        if lower < upper:
            strCmd  +=  "-l %s -u %s -r " % (lower, upper)
        elif autoScale:
            strCmd += "-A "
        strCmd += "-Y "

        # Set the chart ordinate label and chart title. 
        strCmd += "-v %s -t %s " % (gLabel, gTitle)
     
        # Show the data, or a moving average trend line over
        # the data, or both.
        strCmd += "DEF:dSeries=%s:%s:LAST " % (self.rrdFile, dataItem)
        if addTrend == 0:
            strCmd += "LINE1:dSeries#0400ff "
        elif addTrend == 1:
            strCmd += "CDEF:smoothed=dSeries,%s,TREND LINE2:smoothed#006600 " \
                      % trendWindow[gStart]
        elif addTrend == 2:
            strCmd += "LINE1:dSeries#0400ff "
            strCmd += "CDEF:smoothed=dSeries,%s,TREND LINE2:smoothed#006600 " \
                      % trendWindow[gStart]
         
        if self.debugMode:
            print("%s" % strCmd) # DEBUG
        
        # Run the formatted rrdtool command as a subprocess.
        try:
            result = subprocess.check_output(strCmd, \
                         stderr=subprocess.STDOUT,   \
                         shell=True)
        except subprocess.CalledProcessError as exError:
            print("rrdtool graph failed: %s" % (exError.output.decode('utf-8')))
            return False

        if self.verboseMode:
            print("rrdtool graph: %s" % result.decode('utf-8')) #, end='')
        return True

    ##end def
## end class

