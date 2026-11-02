#!/usr/bin/python3 -u
## -*- coding: utf-8 -*
"""
"""

import smbus
import time

# Bus address of rainfall sensor
_I2C_BUS_ADDR = 0x1D
_BUS_NUMBER = 1
  
# Rainfall sensor register addresses
# PID
_PID                 = 0x00
# VID
_VID                 = 0x02
# Firmware version.
_FIRMWARE_VERSION    = 0x0A
# The cumulative rainfall within the set time
_TIME_RAINFALL      = 0x0C
# The cumulative rainfall since starting work
_CUMULATIVE_RAINFALL = 0x10
# The the raw number of bucket tips
_RAW_DATA            = 0x14
# The system time
_SYS_TIME            = 0x18
  
# The time interval for calculating the cumulative rainfall
_RAW_RAIN_HOUR       = 0x26
# The initial value of the accumulated rainfall
_RAW_BASE_RAINFALL   = 0x28


class sen0575:
  '''!
    @brief Define DFRobot_RainfallSensor basic class
    @details Drive the sensor
  '''
  def __init__(self, sbus=_BUS_NUMBER, debug=False):
    self.vid = 0
    self.pid = 0
    self.i2c = smbus.SMBus(sbus)
  
  def begin(self):
    '''!
      @brief This function will attempt to communicate with a slave device 
             and determine if the communication is successful based on the
             return value.
      @return Communication result
      @retval true  Succeed
      @retval false Failed
    '''
    return self.get_pid_vid()

  def get_firmware_version(self):
    '''!
      @brief  get firmware version
      @return  Return  firmware version
      
      Version extracted from two bytes as follows:

         | bbbb | bbbb | bbbb | bbbb |
            |      |      |      |
       v .  n   .  n   .  n   .  n

      For example, 0001001000110100 would be
      version v.1.2.3.4 
    '''
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _FIRMWARE_VERSION, 2)
    version = lData[0] | ( lData[1] << 8 )
    return str(version >> 12) + '.'+ str( ( (version >> 8) & 0x0F ) ) + \
           '.' + str( ( (version >> 4) & 0x0F ) ) + '.' + str( (version & 0x0F) )

  def get_pid_vid(self):
    '''!
      @brief    Get the PID and VID of the device.
      @return   Return true if the data is obtained correctly, 
                false if failed or incorrect data.
      
      PID and VID extracted from four bytes as follows.  Concatenating the four bytes
      received from the sensor into a thirty two bit word, b0 is the most significant
      bit of the first element in the list object returned by the I2C read function.
      
      byte 0 | b0  ... b7  |
      byte 1 | b8  ... b15 |
      byte 2 | b16 ... b23 |
      byte 3 | b24 ... b31 |
      
      PID = b24,b25,b8...b15,b0...b7
      VID = b26...b31,b8...b15
    '''
    ret = False
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _PID, 4)
    self.pid = lData[0] | ( lData[1] << 8 ) | ( ( lData[3] & 0xC0 ) << 10 )
    self.vid = lData[2] | ( ( lData[3] & 0x3F ) << 8 )
    if (self.vid == 0x3343) and (self.pid == 0x100C0):
      ret = True
    return ret

  def get_sensor_working_time(self):
    '''!
      @brief Obtain the sensor working time
      @return Working time of the sensor, in hours
      
      Working time extracted from two bytes in positive integer format.
      Integer value represents whole minutes.  Divide by 60.0 gives
      value in hours.
    '''
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _SYS_TIME, 2)
    working_time = lData[0] | (lData[1] << 8)
    return working_time / 60.0

  def get_rainfall(self):
    '''!
      @brief  Get cumulative rainfall
      @return Cumulative rainfall value
      
      Cumulative rainfall extracted as a four byte positive integer
      as follows.
  
      byte 0 | b0  ... b7  |
      byte 1 | b8  ... b15 |
      byte 2 | b16 ... b23 |
      byte 3 | b24 ... b31 |
        
      rainfall [raw] = b31...b0
      
      Dividing by 10000.0 gives rainfall in millimeters.
    '''
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _CUMULATIVE_RAINFALL, 4)
    rainfall = lData[0] | ( lData[1] << 8 ) | ( lData[2] << 16 ) | ( lData[3] << 24 )
    return rainfall / 10000.0

  def get_rainfall_time(self,hour):
    '''!
      @brief  Get the cumulative rainfall within the specified time
      @param hour Specified time (valid range is 1-24 hours)
      @return Cumulative rainfall

      Cumulative rainfall for the specified time extracted as a four
      byte positive integer as follows.
  
      byte 0 | b0  ... b7  |
      byte 1 | b8  ... b15 |
      byte 2 | b16 ... b23 |
      byte 3 | b24 ... b31 |
        
      rainfall [raw] = b31...b0
      
      Dividing by 10000.0 gives rainfall in millimeters.
    '''
    if hour>24:
      return 0
    lData = [hour]

    self.i2c.write_i2c_block_data(_I2C_BUS_ADDR, _RAW_RAIN_HOUR, lData)
    time.sleep(0.050)
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _TIME_RAINFALL, 4)
    rainfall = lData[0] | ( lData[1] << 8 ) | ( lData[2] << 16 ) | ( lData[3] << 24 )
    return rainfall / 10000.0

  def get_raw_data(self):
    '''!
      @brief Get raw data
      @return Number of tipping bucket counts, unit: count
      
      Raw data, i.e., number of tipping bucket counts extracted
      as a four byte positive integer as follows.
  
      byte 0 | b0  ... b7  |
      byte 1 | b8  ... b15 |
      byte 2 | b16 ... b23 |
      byte 3 | b24 ... b31 |
        
      tipping bucket counts = b31...b0
     '''
    lData = self.i2c.read_i2c_block_data(_I2C_BUS_ADDR, _RAW_DATA, 4)
    raw_data = lData[0] | ( lData[1] << 8 ) | ( lData[2] << 16 ) | ( lData[3] << 24 )
    return raw_data

  def set_rain_accumulated_value(self, value):
    '''!
      @brief Set the rainfall accumulation value
      @param value Rainfall accumulation value, in millimeters
      @return Returns 0 if setting succeeded, other values if setting failed

      Sets a conversion factor that converts a bucket tip into millimeters or
      inches of rain.
  
      Value gets multiplied by 10000 and stored in a two element
      list object, each element being one byte.
     
      byte 0 | b7  ... b0  |
      byte 1 | b15 ... b8 |
    '''
    data = int(value * 10000)
    lData=[data & 0xFF , data >> 8]
    self.i2c.write_i2c_block_data(_I2C_BUS_ADDR, _RAW_BASE_RAINFALL, lData)
    time.sleep(0.050)
    return ret

def test():
  import time
  
  sensor = sen0575()
  
  if (sensor.begin() == False):
    print("Sensor initialize failed!!")
  else:
    print("Sensor  initialize success!!")
    print("Version: "+ sensor.get_firmware_version())
    print("vid: %#x"%(sensor.vid))
    print("pid: %#x"%(sensor.pid))

  while True:
    # Get the sensor operating time in units of hours.
    workingtime = sensor.get_sensor_working_time()
    # Get the cumulative rainfall during the sensor operating time.
    rainfall=sensor.get_rainfall()
    # Here is an example function that calculates the cumulative rainfall in a
    # specified hour of the system. The function takes an optional argument,
    # which can be any value between 1 and 24.
    one_hour_rainfall=sensor.get_rainfall_time(1)

    # Get the raw data, which is the tipping bucket count of rainfall, in units of counts.
    rainfall_raw=sensor.get_raw_data()

    print("workingtime : %f H"%(workingtime))
    print("rainfall : %f mm"%(rainfall))
    print("The amount of rain in one hour: %f mm"%(one_hour_rainfall))
    print("Original value collected by rain gauge %d"%(rainfall_raw))
    print("--------------------------------------------------------------------")
    time.sleep(1)
  
if __name__ == "__main__":
  test()

