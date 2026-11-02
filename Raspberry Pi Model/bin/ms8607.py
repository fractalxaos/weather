#!/usr/bin/python3 -u
#
# Module: ms8607.py
#
# Description: This module acts as an interface between the MS8607
# pressure, temperature, humidity sensor and downstream applications
# that use the data.  Class methods get pressure, altitude, and
# temperature data from the MS8607 sensor.  This module acts as a
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
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see http://www.gnu.org/license.
#
# Revision History
#   * v10 released 08 Oct 2021 by J L Owrey: first release
#   * v11 released 12 Jun 2025 by J L Owrey: both pressure and temperature now
#         computed in the same function, added human readable names for I2C
#         commands in heater function
#
#2345678901234567890123456789012345678901234567890123456789012345678901234567890

import smbus
import time

# Define default sm bus address.
_PT_BUS_ADDRESS = 0x76
_RH_BUS_ADDRESS = 0x40
_DEFAULT_BUS_NUMBER = 1

# Normalize barometric pressure to sea level.
_BAROMETRIC_ALTITUDE_CORRECTION = 0.2

# Define delay time for sensor coversion time.
_SENSOR_PROCESSING_TIME = 0.02

# Reset commands
_PT_RESET = 0x1E
_RH_RESET = 0xFE

# Pressure conversion commands
_P_D1_OSR256_CONVERT = 0x40
_P_D1_OSR512_CONVERT = 0x42
_P_D1_OSR1024_CONVERT = 0x44
_P_D1_OSR2048_CONVERT = 0x46
_P_D1_OSR4096_CONVERT = 0x48
_P_D1_OSR8192_CONVERT = 0x4A

# Temperature conversion commands
_T_D2_OSR256_CONVERT = 0x50
_T_D2_OSR512_CONVERT = 0x52
_T_D2_OSR1024_CONVERT = 0x54
_T_D2_OSR2048_CONVERT = 0x56
_T_D2_OSR4096_CONVERT = 0x58
_T_D2_OSR8192_CONVERT = 0x5A

# Read ADC command.
_PT_ADC_READ = 0x00

# PROM addresses for pressure and temperature conversion coefficients
_PROM_SENS_T1 = 0xA2 # Pressure sensitivity
_PROM_OFF_T1 = 0xA4 # Pressure offset
_PROM_TCS = 0xA6 # Temperature coefficient of pressure sensitivity
_PROM_TCO = 0xA8 # Temperature coefficient of pressure offset
_PROM_T_REF = 0xAA # Reference temperature
_PROM_TEMP_SENS = 0xAC # Temperature coefficient of the temperature

# Relative humidity commands
_WRITE_USER_REG = 0xE6
_READ_USER_REG = 0xE7
_MEASURE_RH_HOLD = 0xE5
_MEASURE_RH_NO_HOLD = 0xF5


class ms8607:

    def __init__(self, sbus=_DEFAULT_BUS_NUMBER, debug=False):
        self.bus = smbus.SMBus(sbus)
        self.debugMode = debug
        self.resetRH()
        self.resetPT()
        self.getConversionCoefficients()
    ## end def

    def resetPT(self):
        self.bus.write_byte(_PT_BUS_ADDRESS, _PT_RESET)
        time.sleep(_SENSOR_PROCESSING_TIME)
    ## end def

    def resetRH(self):
        self.bus.write_byte(_RH_BUS_ADDRESS, _RH_RESET)
        time.sleep(_SENSOR_PROCESSING_TIME)
    ## end def

    def getConversionCoefficients(self):
        # Read pressure and temperature conversion coefficients from
        # the MS8607 read only memory (PROM). Returned list looks like
        # [0, 41390, 40561, 25663, 26068, 28830, 27803]

        # To make the 'c' list object element indicies correspond to the
        # coefficient subscripts in the datasheet, define zeros for c[0].
        c = [[0,0]]  # not used

        # C1 | Pressure sensitivity | SENS_T1
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_SENS_T1, 2))
        # C2 | Pressure offset | OFF_T1
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_OFF_T1, 2))
        # C3 | Temperature coefficient of pressure sensitivity | TCS
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_TCS, 2))
        # C4 | Temperature coefficient of pressure offset | TCO
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_TCO, 2))
        # C5 | Reference temperature | T_REF
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_T_REF, 2))
        # C6 | Temperature coefficient of the temperature | TEMP_SENS
        c.append(self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, \
                  _PROM_TEMP_SENS, 2))

        # Convert coefficient data from two bytes for each coefficient
        # to one word (16 bits) for each coefficient.  The first byte
        # i.e, c[i][0], in the list object is the MSB.
        for i in range(len(c)):
            c[i] = c[i][0] << 8 | c[i][1]
        self.c = c
    ## end def

    def getPTdata(self, p_osr=_P_D1_OSR256_CONVERT, t_osr=_T_D2_OSR256_CONVERT):

        # Initiate pressure conversion.
        self.bus.write_byte(_PT_BUS_ADDRESS, p_osr)
        time.sleep(_SENSOR_PROCESSING_TIME)
        # Get pressure data from ADC.
        data = self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, _PT_ADC_READ, 3)
        D1 = data[0] << 16 | data[1] << 8 | data[0]

        # Initiate temperature conversion.
        self.bus.write_byte(_PT_BUS_ADDRESS, t_osr)
        time.sleep(_SENSOR_PROCESSING_TIME)
        # Get temperature data from ADC.
        data = self.bus.read_i2c_block_data(_PT_BUS_ADDRESS, _PT_ADC_READ, 3)
        D2 = data[0] << 16 | data[1] << 8 | data[0]

        # Calculate difference between actual and reference temperature.
        dT = D2 - self.c[5] * 256
        # Calculate first order temperature.
        TEMP = 2000 + dT * self.c[6] / 8388608
 
        # Calculate first order pressure compensation
        OFF = self.c[2] * 131072 + (self.c[4] * dT) / 64
        SENS = self.c[1] * 65536 + (self.c[3] * dT) / 128

        PRESSURE = ((D1 * SENS / 2097152) - OFF) / 32768

        if self.debugMode:
            #print("1st order PT: ", (PRESSURE/100, TEMP/100))
            pass

        # Calculate 2nd order pressure compensation over temperature. According
        # to the MS8607 datasheet, to optimize accuracy at low temperatures,
        # the pressure should be compensated non-linearity over temperature.   
        if TEMP < 2000:
            # Low temperature
            T2 = 3 * (dT**2) / 8589934592
            OFF2 = 61 * (TEMP - 2000)**2 / 16
            SENS2 = 29 * (TEMP - 2000)**2 / 16
            if TEMP < -1500:
                # Very low temperature
                OFF2 = OFF2 + 17 * (TEMP + 1500)**2
                SENS2 = SENS2 + 9 * (TEMP + 1500)**2
        else:
            # High temperature
            T2 = 5 * (dT**2) / 274877906944
            OFF2 = 0
            SENS2 = 0
            # Calculate pressure and temperature
        TEMP = TEMP - T2
        OFF = OFF - OFF2
        SENS = SENS - SENS2

        # Calculate pressure in millbars.
        PRESSURE = ((D1 * SENS / 2097152) - OFF) / 32768

        if self.debugMode:
            #print("2st order PT: ", (PRESSURE/100, TEMP/100))
            pass

        return (PRESSURE, TEMP)
    ## end def

    def getTempC(self, p_osr=_P_D1_OSR4096_CONVERT, t_osr=_T_D2_OSR4096_CONVERT):
        # Convert temperature to degrees Celsius.
        (PRESSURE, TEMP) = self.getPTdata(p_osr, t_osr)
        return TEMP/100
    ## end def

    def getTempF(self, p_osr=_P_D1_OSR4096_CONVERT, t_osr=_T_D2_OSR4096_CONVERT):
        # Convert temperature to degrees Fahrenheit.
        (PRESSURE, TEMP) = self.getPTdata(p_osr, t_osr)
        return 1.8 * TEMP/100 + 32
    ## end def

    def getPressureMbar(self, p_osr=_P_D1_OSR4096_CONVERT, t_osr=_T_D2_OSR4096_CONVERT):
        # Convert millibars to inches of mercury.
        (PRESSURE, TEMP) = self.getPTdata(p_osr, t_osr)
        return PRESSURE/100
    ## end def

    def getPressureBaro(self, p_osr=_P_D1_OSR4096_CONVERT, t_osr=_T_D2_OSR4096_CONVERT):
        # Convert millibars to inches of mercury.
        (PRESSURE, TEMP) = self.getPTdata(p_osr, t_osr) 
        return PRESSURE * 0.02953/100
    ## end def

    def getHumidity(self, command=_MEASURE_RH_NO_HOLD):
        # Initiate humidity conversion.

        self.bus.write_byte(_RH_BUS_ADDRESS, command)
        time.sleep(_SENSOR_PROCESSING_TIME)

        # Get humidity data.
        # Sixteen bit resolution not implemented by MS8607 chip supplied.
        # Uncomment if this feature becomes available.
        #data = self.bus.read_i2c_block_data(_RH_BUS_ADDRESS, 0, 2)
        #D3 = data[0] << 6 | data[1] >> 2

        # Poorly documented in the datasheet, only eight bit humidity
        # data implemented by the MS8607 chip supplied.
        data = self.bus.read_byte(_RH_BUS_ADDRESS)
        D3 = data << 8
        #print('D3: %s' % format(D3, "016b"))

        # Calcualte relative humidity.
        RH = -600 + 12500 * D3 / 65536.0
        return RH / 100.0
    ## end def

    def readUserReg(self):
        # Send the command to read the user register.
        self.bus.write_byte(_RH_BUS_ADDRESS, _READ_USER_REG)
        time.sleep(_SENSOR_PROCESSING_TIME)
        # Read the register.
        data = self.bus.read_byte(_RH_BUS_ADDRESS)
        return data
    ## end def

    def heater(self, state=False):
        # Get user register contents.  Bit 2 turns the heater on or off.
        # All other bits must stay the same, that is, not be changed.
        userReg = self.readUserReg()
       
        if state:
          # Turn on heater by setting bit 2 of the user register to 1.
          userReg = userReg | 0x04
          self.bus.write_i2c_block_data(_RH_BUS_ADDRESS, _WRITE_USER_REG, [userReg])
        else:
          # Turn off heater by setting bit 2 of the user register to 0.
          userReg = userReg & 0xFB
          self.bus.write_i2c_block_data(_RH_BUS_ADDRESS, _WRITE_USER_REG, [userReg])
        time.sleep(0.1)
    ## end def
## end class

def test():
    tph1 = ms8607(debug=True)

    print('user reg: %s' % format(tph1.readUserReg(), '08b'))

    print('\nTurning on heater...') 
    tph1.heater(True)
    print('user reg: %s\n' % format(tph1.readUserReg(), '08b'))

    for iloop in range(10):
        print("Temperature compensated Pressure is : %.2f mbar" % \
            tph1.getPressureMbar())
        print("Barometric Pressure is : %.2f in Hg" % \
            (tph1.getPressureBaro() + _BAROMETRIC_ALTITUDE_CORRECTION))
        print("Temperature in Celsius : %.2f C" % tph1.getTempC())
        print("Temperature in Fahrenheit : %.2f F" % tph1.getTempF())
        print("Relative Humidity : %.2f %%" % tph1.getHumidity())
        print()
        time.sleep(2)

    print('\nTurning off heater...') 
    tph1.heater(False)
    print('user reg: %s\n' % format(tph1.readUserReg(), '08b'))
  
    while True:
        print("Temperature compensated Pressure is : %.2f mbar" % \
            tph1.getPressureMbar())
        print("Barometric Pressure is : %.2f in Hg" % \
            (tph1.getPressureBaro() + _BAROMETRIC_ALTITUDE_CORRECTION))
        print("Temperature in Celsius : %.2f C" % tph1.getTempC())
        print("Temperature in Fahrenheit : %.2f F" % tph1.getTempF())
        print("Relative Humidity : %.2f %%" % tph1.getHumidity())
        print()
        time.sleep(2)
 ## end def

if __name__ == '__main__':
    try:    
        test()
    except KeyboardInterrupt:
        #del tph1
        print()  # clean up on CTRL+C exit



