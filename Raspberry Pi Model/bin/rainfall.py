#!/usr/bin/python3 -u
#
# Module: DFRobot_RainfallSensor
#
# Description: This module acts as an interface between the DFRobot
# rainfall sensor and downstream applications that use this data.
# Class methods get rainfall data from the DFRobot sensor and
# calculate hourly and daily rainfall amounts.
# This module acts as a library module that can be imported into and
# called from other Python programs.
#
# Rain sensor connected to the I2C interface.  Serial data connected to
# GPIO 2, serial clock to GPIO 3, VDD to 3V3 power, ground to ground. 
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
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see http://www.gnu.org/license.
#
# Revision History
#   * v10 released 08 Oct 2021 by J L Owrey: first release
#
#2345678901234567890123456789012345678901234567890123456789012345678901234567890

import time
from sen0575 import *


# Sets default DFRobot rain sensor polling interval
_DEFAULT_DATA_REQUEST_INTERVAL = 10
# Rain sensor conversion factor
_RAIN_SENS_INCHES_PER_TIP = 0.011
# Seconds per hour
_SECONDS_PER_HOUR = 3600

class rainfall:

    def __init__(self, dataRequestInterval=_DEFAULT_DATA_REQUEST_INTERVAL,
        debug=False):
        
        # Create an instance of the rain sensor driver object.
        self.sensor=sen0575()

        # Verify the rain sensor is present and working.
        if self.sensor.begin() == False:
            print("%s Rain sensor not connected or not detected" \
                  % (getTimeStamp()))
            raise Exception("rainfall sensor error")
        elif debug == True: 
            print("Rain sensor detected")

        # Calculate size of hourly rainfall aggregation array.
        self.hourlyRainArraySize = int(_SECONDS_PER_HOUR / dataRequestInterval)
        # Initialize aggregation array.
        self.hourlyRainArray = [0] * self.hourlyRainArraySize
        # Initialize aggregation array location pointer.
        self.rainArrayPtr = 0

        # Initialize daily bucket tip counter.
        self.dailyTipCount = 0

        # Get initial bucket tip count
        self.previousTipCount = self.sensor.get_raw_data()
        
        # Set debug output mode.
        self.debugMode = debug
    ## end def

    def updateHourlyRain(self):
        """
        Description:  Updates the aggregate hourly rain for the previous
          sixty minute period.  The update function should be called
          once every update polling period, as defined by the
          dataRequestInterval initialization parameter.
        Parameters: None
        Returns: Nothing
        """
        # Get number of bucket tips that occured during previous polling
        # period.
        self.tipCount = self.sensor.get_raw_data() - self.previousTipCount
        self.previousTipCount = self.sensor.get_raw_data()
        
        # Add rainfall during current polling period to the 
        # daily rainfall amount in terms of bucket tip counts.
        self.dailyTipCount += self.tipCount
        
        # Store the number of click events that have occured since the
        # previous hourly rainfall array update.
        self.hourlyRainArray[self.rainArrayPtr] = self.tipCount
        # Advance the hourly rainfall array pointer.
        self.rainArrayPtr += 1
        # When the array is full, reset the pointer back to the
        # beginning of the array.  This implements a round-robin
        # memory structure.
        if self.rainArrayPtr > self.hourlyRainArraySize - 1:
            self.rainArrayPtr = 0;
  
        if self.debugMode:
            # Print out the rainfall aggregator array.
            print(self.hourlyRainArray)
            pass
    ## end def

    def getHourlyRain(self):
        """
        Description:  Calculates the total amount of rain that has
          fallen in the previous sixty minutes.
        Parameters: None
        Returns: The hourly rainfall in inches.
        """
        # Calculate the total amount of rainfall that has fallen
        # in the last hour, that is, in the last sixty minutes.
        sum = 0
        for i in range(self.hourlyRainArraySize):
            sum += self.hourlyRainArray[i]
        return float(sum) * _RAIN_SENS_INCHES_PER_TIP
    ## end def

    def getDailyRain(self):
        """
        Description:  Calculates the total amount of rain that has
          fallen since the daily rain total since the last time
          that the resetDailyRain function was called.
        Parameters: None
        Returns: The daily rainfall in inches.
        """
        # Calculate the total rainfall that has fallen since the
        # beginning of the day, that is, since 12 AM.
        return self.dailyTipCount * _RAIN_SENS_INCHES_PER_TIP
    ## end def

    def resetDailyRain(self):
        """
        Description:  Resets the daily rainfall value to zero.
          Normally this would be called once a day,.e.g, once a
          day at midnight.
        Parameters: None
        Returns: Nothing
        """
        self.dailyTipCount = 0
    ## end def

    def getTimeStamp(self):
        """Sets the error message time stamp to the local system time.
           Parameters: none
           Returns: string containing the time stamp
        """
        return time.strftime('%m/%d/%Y %H:%M:%S', time.localtime())
    ## end def
## end class

### Test functions ###

def test1():
    # Test normal opertion of the rain sensor and DFRobot_RainfallSensor class.

    interval = 10 # sec
    # Instantiate rain sensor object.
    rn1 = rainfall(dataRequestInterval=interval, debug=True)
    # Test rainfall sensor interrupt event processor.
    rn1.resetDailyRain()
    # Periodically display rainfall values.
    try:
        while True:
            rn1.updateHourlyRain()
            hrRain = rn1.getHourlyRain()
            daRain = rn1.getDailyRain()
            print('hrRain: %.3f  daRain: %.3f' % (hrRain, daRain))
            time.sleep(interval)
    except KeyboardInterrupt:
        del rn1
## end def

def test2():
    # Test rainfall array functions.

    # Instantiate rain sensor object.
    rn1 = rainfall(debug=True)
    
    for i in range(360):
        rn1.tipCount = i
        rn1.updateHourlyRain()
        hCnts = rn1.tipCount
        dCnts = rn1.dailyTipCount
        hrRain = rn1.getHourlyRain()
        daRain = rn1.getDailyRain()
        print('hr %d cnts %.3f in\tda %d cnts %.3f in' % (hCnts, hrRain, dCnts, daRain))
    print()
    for i in range(360):
        rn1.tipCount = 0
        rn1.updateHourlyRain()
        hCnts = rn1.tipCount
        dCnts = rn1.dailyTipCount
        hrRain = rn1.getHourlyRain()
        daRain = rn1.getDailyRain()
        print('hr %d cnts %.2f in\tda %d cnts %.2f in' % (hCnts, hrRain, dCnts, daRain))

    del rn1
## end def

if __name__ == '__main__':
    test1()
    #test2()
        
