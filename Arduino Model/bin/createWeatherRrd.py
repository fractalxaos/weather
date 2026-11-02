#!/usr/bin/python -u
## The -u option turns off block buffering of python output. This assures
## that error messages get printed to the log file as they happen.
#  
# Module: createWeatherRrd.py
#
# Description: Creates a rrdtool database for use by the weather agent to
# store the data from the weather station.  The agent uses the data in the
# database to generate graphic charts for display in the weather station
# web page.
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
#   * v10 released 15 Sep 2015 by J L Owrey
#
# Example of rrdtool create database command line executed by this program:
#
#   rrdtool create /home/jeff/database/weatherData.rrd --step 60
#   DS:windspeedmph:GAUGE:120:U:U DS:winddir:GAUGE:120:U:U 
#   DS:tempf:GAUGE:120:U:U DS:rainin:GAUGE:120:U:U DS:pressure:GAUGE:120:U:U
#   DS:humidity:GAUGE:120:U:U RRA:AVERAGE:0.5:1:2880 RRA:AVERAGE:0.5:30:17760
#

import os
import time
import subprocess
import math

    ### DEFINE CONSTANTS ###

_RRD_FILE = "weatherData.rrd"
_RRD_SIZE_IN_DAYS = 370 # days
_1YR_RRA_STEPS_PER_DAY = 48
_DATABASE_UPDATE_INTERVAL = 60 # sec

def createRrdFile():
    """Creates a rrdtool round robin database file.  The file when
       first created does not contain any data.  The file will be
       written to the ~/database folder.
       Parameters: none
       Returns: True, if successful
    """

    if os.path.exists(_RRD_FILE):
        print "rrdtool weather database file already exists!"
        return True

    ## Calculate database size
 
    heartBeat = 2 * _DATABASE_UPDATE_INTERVAL
    rra1yrNumPDP =  int(round(86400 / (_1YR_RRA_STEPS_PER_DAY * 
                        _DATABASE_UPDATE_INTERVAL)))
    rrd48hrNumRows = int(2 * round(86400 / _DATABASE_UPDATE_INTERVAL))
    rrd1yearNumRows = _1YR_RRA_STEPS_PER_DAY * _RRD_SIZE_IN_DAYS
       
    # Format the rrdtool create command.
    strFmt = ("rrdtool create %s --step %s "
             "DS:windspeedmph:GAUGE:%s:U:U DS:winddir:GAUGE:%s:U:U "
             "DS:tempf:GAUGE:%s:U:U DS:rainin:GAUGE:%s:U:U "
             "DS:pressure:GAUGE:%s:U:U DS:humidity:GAUGE:%s:U:U "
             "RRA:AVERAGE:0.5:1:%s RRA:AVERAGE:0.5:%s:%s")

    strCmd = strFmt % (_RRD_FILE, _DATABASE_UPDATE_INTERVAL, \
                heartBeat, heartBeat, heartBeat, heartBeat, heartBeat, \
                heartBeat, rrd48hrNumRows, rra1yrNumPDP, rrd1yearNumRows)
    
    print "Creating rrdtool database...\n\n%s\n" % strCmd # DEBUG

    # Run the command in a subprocess.
    try:
        subprocess.check_output(strCmd, stderr=subprocess.STDOUT, \
                                shell=True)
    except subprocess.CalledProcessError, exError:
        print "rrdtool create failed: %s" % (exError.output)
        return False
    return True
##end def

def main():
    createRrdFile()
## end def

if __name__ == '__main__':
    main()

