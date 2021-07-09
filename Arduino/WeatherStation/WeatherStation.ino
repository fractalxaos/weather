/*
 DIY Weather Station
  
 Copyright 2016 Jeff Owrey
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see http://www.gnu.org/license.
 
 Circuit:
 * Main components: Arduino Uno, Wifi shield, Weather shield
 * See project description document for details
   
 Revision History:  
   * v2.0 released 27 Feb 2016 by J L Owrey: first release.  This is the
          server model where the weather station acts as an http server
          answering requests from http clients.
   * v2.1 released 22 Nov 2017 by J L Owrey.  Timeout added to reset the
          wifi interface if http requests are not getting through.  This
          handles situations where the router rescinds the wifi interface's
          IP address lease.  Change wind speed data types from float to byte.
          Modify station password to be a four character PIN.
  * v2.2  released 29 Nov 2020 by J L Owrey.  Improved maintenance command
          handling.  In extended url station password now comes before the  
          maintenance command.
*/

/***  PREPROCESSOR DEFINES  ***/

/*
  Uncomment to turn on output of verbose debug information to the usb
  serial port.  Only use this option for development and test.
*/
//#define DEBUG

/*
  Uncomment to have the ESP8266 do a hard reset only if not already
  connected to a wifi access point.
*/
//#define SOFT_CONNECT

/*
 * System configuration defines
 */

/*
  The amount of time the weather station waits for an  http client
  request before initiating a soft reboot.
*/
#define HTTP_NO_REQUEST_TIMEOUT 300 // seconds
/*
  The amount of time (in milliseconds) the weather station waits before
  initiating a soft reboot.  Controls the minimum cycle time between soft
  reboots.
*/
#define SOFT_REBOOT_WAIT_TIME 10000 // millseconds


/*
 * Library Includes
 */

#include <EEPROM.h>;
#include <Wire.h>
#include "MPL3115A2.h" //Pressure sensor
#include "HTU21D.h" //Humidity sensor
#include <SoftwareSerial.h> 
#include <SparkFunESP8266WiFi.h>

/*
 * Constants
 */

// EEPROM data locations
#define STATION_PWD 0
#define SS_ID 18
#define WPA_PASSWD 52
#define HTTP_HEADER 120 

// Array depths
#define WIND_2M_ARRAY_DEPTH 120
#define RAIN_1H_ARRAY_DEPTH 60
#define GUST_10M_ARRAY_DEPTH 10
#define REPORT_STRING_BUFFER_LENGTH 74

// Constants
#define WIND_SENSOR_DEBOUNCE_SETTLING_TIME 10 // milliseconds
#define RAIN_SENSOR_DEBOUNCE_SETTLING_TIME 50 // milliseconds
#define HTTP_CONNECTION_TIMEOUT 500 // milliseconds
#define RAIN_CLICK_CONVERSION_FACTOR 0.011 // inches per click
#define WIND_CLICK_CONVERSION_FACTOR 1.492 // miles per hour per click

// GPIO assignments - Arduino pinout definitions
#define WIND_CLICKS 3
#define RAIN_CLICKS 2
#define STATUS_LED 7
#define WIND_DIRECTION A0
#define LIGHT_LEVEL A1
#define BATTERY_LEVEL A2
#define REFERENCE_3V A3

/*
 * Global Variables
 */
 
// HTTP Server
ESP8266Server server = ESP8266Server(80);

// I2C sensors
MPL3115A2 aPressureSensor; //Create an instance of the pressure sensor
HTU21D aHumiditySensor; //Create an instance of the humidity sensor

// Interrupt Handlers
// rain
volatile byte irqRainClicks;
volatile word irqTotalRainClicks;
volatile unsigned long lastRainClickTime;
// wind
volatile byte irqWindClicks;
volatile unsigned long lastWindClickTime;

// Housekeeping
byte seconds;
byte minutes;
unsigned long lastLoopCycleTime;
unsigned long lastWindCheckTime;
byte ptr2mWind;
byte ptr1hRain;
byte ptr10mGust;
int prevClientRequestTime;

// Weather data aggregation arrays
byte arrWindSpeed2mAv[WIND_2M_ARRAY_DEPTH];
byte arrWindDirection2mAv[WIND_2M_ARRAY_DEPTH];
byte arrWindGustSpeed10m[GUST_10M_ARRAY_DEPTH];
byte arrWindGustDirection10m[GUST_10M_ARRAY_DEPTH];
byte arrRainPerHour[RAIN_1H_ARRAY_DEPTH];

// Weather data items - specified by Weather Underground
byte windSpeed;
byte windDirection;
byte windSpeed2mAv;
byte windDirection2mAv;
byte windGustSpeed;
byte windGustDirection;
byte windGustSpeedMax10m;
byte windGustDirectionMax10m;
float tempf;
float humidity;
float pressure;
float rainPerHour;
float rainDayTotal;

// Additional data items
float batteryLevel;
float lightLevel;

void WindSpeedIRQ() {
  /*
   * Wind sensor interrupt handler routine. This routine increments
   * a counter, provided it has not been called previously in less
   * than a specified time.  This specified time filters out switch
   * bounce from the wind sensor.
   * 
   * Parameters: none
   * Returns nothing.
   */
  // system time (msec) when interrupt handler called
  unsigned long currentTime;

  currentTime = millis();
  if (abs(currentTime - lastWindClickTime) > 
      WIND_SENSOR_DEBOUNCE_SETTLING_TIME) {
    lastWindClickTime = currentTime;
    irqWindClicks += 1;
  }
}

void RainFallIRQ() {
  /*
   * Rain fall sensor interrupt handler routine. This routine increments
   * a counter, provided it has not been called previously in less
   * than a specified time.  This specified time filters out switch
   * bounce from the rain fall sensor.
   * 
   * Parameters: none
   * Returns nothing.
   */
   // system time (msec) when interrupt handler called
   unsigned long currentTime;

  currentTime = millis();
  if (abs(currentTime - lastRainClickTime) > 
      RAIN_SENSOR_DEBOUNCE_SETTLING_TIME) {
    lastRainClickTime = currentTime;
    irqRainClicks += 1;
    irqTotalRainClicks += 1;
  }
}

void setup() {
  /*
   * This routine gets called only once when the Uno starts after power on
   * or a software reset.  In summary, this routine does the following: 
   *     - start up the usb serial port (used for debugging)
   *     - define gpio modes
   *     - set up the wifi adapter
   *     - start up sensor devices
   *     - turn on interrupt handlers
   *     - initialize global variables
   *     
   * Parameters: none
   * Returns nothing.
   */
 
  // start up USB serial port
  Serial.begin(9600);
  Serial.println(F("\nDIY weather v2.2"));
  Serial.println(F("(c) 2020 intravisions.com\n"));
  
  // setup gpio
  pinMode(WIND_CLICKS, INPUT_PULLUP);
  pinMode(WIND_DIRECTION, INPUT);
  pinMode(RAIN_CLICKS, INPUT_PULLUP);
  pinMode(LIGHT_LEVEL, INPUT);
  pinMode(BATTERY_LEVEL, INPUT);
  pinMode(REFERENCE_3V, INPUT);

  // initialize wifi adapter
  initializeWifiAdapter();
  
  // connect to the stored wifi access point
  connectWifiAccessPoint();

  // display wifi connection information
  displayWifiConnection();

  // start HTTP server
  server.begin();
  Serial.println(F("Waiting for client request..."));
   
  // start up and configure the pressure sensor
  // Get sensor online
  aPressureSensor.begin();
  // Measure pressure in Pascals from 20 to 110 kPa
  aPressureSensor.setModeBarometer();
  // Set Oversample to the recommended 128
  aPressureSensor.setOversampleRate(7);
  // Enable all three pressure and temp event flags
  aPressureSensor.enableEventFlags(); 

  // start up the humidity sensor
  aHumiditySensor.begin();

  // turn on interrupt handlers
  attachInterrupt(1, WindSpeedIRQ, FALLING);
  attachInterrupt(0, RainFallIRQ, FALLING);

  // initialize global variables used by interrupt handlers

  irqRainClicks = 0;
  irqTotalRainClicks = 0;
  lastRainClickTime = 0;

  irqWindClicks = 0;
  lastWindClickTime = 0;
  prevClientRequestTime = int(millis() / 1000);
  
  lastLoopCycleTime = millis();
  batteryLevel = getBatteryLevel();
  delay(20);
}

void loop() {
  /*
   * This routine gets called repeatedly after the setup routine runs.
   * It runs various routines that must happen at specific, periodic
   * intervals. Events happen with periodicities defined both in seconds
   * and minutes.
   * 
   * Parameters: none
   * Returns nothing.
   */
  unsigned long currentTime; // stores current system time (msec)

  currentTime = millis();
  
  // check for periodic events on one second ticks
  // always want to roll over at the 1000 millisecond mark
  
  if (abs(currentTime - lastLoopCycleTime) > 999) {
    // adjust lastLoopCycleTime for over any over-reach in last cycle
    lastLoopCycleTime = currentTime - (currentTime -
                        lastLoopCycleTime) % 1000;

    doEverySecond(); // events that occur with periodicities in seconds
    if (seconds == 59) {
      doEveryMinute();  // events that occur with periodicities in minutes
    }
    
    // increment seconds counter
    seconds++;
    // roll over seconds counter if 59 seconds have elapsed
    if (seconds > 59) {
      seconds = 0;

      // increment minutes counter upon roll over of seconds counter
      minutes++;
      // roll over minutes counter if 59 minutes have elapsed
      if (minutes > 59) {
        minutes = 0;
      }
    }
  }
  listenForClients();

  delay(20);

  // this helps determine the load on the micro-controller
  #ifdef DEBUG
    Serial.print(F("time: "));Serial.println(millis() - currentTime);
  #endif
}

void doEverySecond() {
  /*
   * This routine triggers events that happen with periodicities defined in
   * intervals of seconds.  In summary, the following events get triggered:
   *     - update the wind gust array with latest wind gust (if any)
   *     - update the 2 minute wind array (used for calculating an average
   *       value of wind over the last 2 minutes)
   *     
   * Parameters: none
   * Returns nothing.
   */
   
  // this helps to determine if wind and rain sensors are working properly
  #ifdef DEBUG
    Serial.print(F("clicks: r ")); Serial.print(irqTotalRainClicks);
    Serial.print(F(" ws "));Serial.print(irqWindClicks);
    Serial.print(F(" wd "));Serial.print(windDirection);
  #endif

  // get instantanious wind speed and direction  
  windSpeed = getWindSpeed();
  windDirection = getWindDirection();
  
  // update 2 minute wind average
  if (seconds % (120 / WIND_2M_ARRAY_DEPTH) == 0) {
    update2minWindAverage();

    // debug
    #ifdef DEBUG_ARRAYS
      displayArrays('s');
    #endif
  }

  // update daily and 10 minute maximum wind gust
  update1secWindGust();
}

void doEveryMinute() {
  /*
   * This routine triggers events that happen with periodicities defined in
   * intervals of minutes.  In summary, the following events get triggered:
   *     - update the wind gust array pointer
   *     - clear the current wind gust array element
   *     - update the one hour rainfall array
   * Parameters: none
   * Returns nothing.
   */

  // update 10 minute wind gust array
  update10minWindGust();
  // update 1 hour rainfall array
  update1hourRainFall();

  // this helps to determine if data arrays are working properly
  #ifdef DEBUG_ARRAYS
    displayArrays('m');
    displayArrays('h');
  #endif
}

void update2minWindAverage() {
  /*
   * Updates the two minute average wind speed and direction arrays. The arrays contain 
   * wind speed and direction data for a two minute period ending at the current time.  
   * This data used to calculate the average wind speed and direction during the last 
   * two minutes.
   * 
   * Parameters: none
   * Returns nothing.
   */
  arrWindSpeed2mAv[ptr2mWind] = windSpeed;
  arrWindDirection2mAv[ptr2mWind] = windDirection;
  /*
    Update the 2 minute array pointer.  This implements a 
    FIFO memory structure with the pointer always pointing
    to the first element in. Reset the pointer back to the
    beginning of the array when it reaches the end of the array.
  */
  ptr2mWind++;
  // roll over the array pointer when the arrays are full
  if (ptr2mWind > WIND_2M_ARRAY_DEPTH - 1) { 
    ptr2mWind = 0;
  }
}

void update1secWindGust() {
  /*
   * Called once every second, this function updates the daily wind gust
   * data and the ten minute wind gust data for the current minute.  Each
   * second the instantaneous wind speed and direction get measured by wind
   * sensors.  If the instantaneous wind speed is greater than the reading
   * from the previous second, then the daily wind gust data gets updated
   * with the new reading.  Similarly, the 10 minute wind gust data also gets
   * updated as follows.  Each element of the 10 minute wind gust array 
   * represents the maximum wind speed measured during a one minute interval.
   * Hence, taking the maximum of the array gives the maximum wind gust
   * measured during the previous ten minute period.
   * As with the daily wind gust, once every second the wind gust array 
   * element holding the maximum gust for the current minute gets updated, 
   * if greater than the previous value stored in the array element.  Note 
   * that both the wind speed and wind direction arrays get updated at the 
   * same time.
   * 
   * Parameters: none
   * Returns nothing.
   */
  // update the current minute in the ten minute wind gust arrays
  if (windSpeed > arrWindGustSpeed10m[ptr10mGust]) {
    arrWindGustSpeed10m[ptr10mGust] = windSpeed;
    arrWindGustDirection10m[ptr10mGust] = windDirection;
  }

  // update the daily wind gust data items
  if (windSpeed > windGustSpeed) {
    windGustSpeed = windSpeed;
    windGustDirection = windDirection;
  }
}

void update10minWindGust() {
  /*
   * Updates the 10 minute wind gust array pointer and clears the next
   * array element to receive new wind gust data.
   * 
   * Parameters: none
   * Returns nothing.
   */
  // update the wind gust array pointer
  ptr10mGust++; 
  // after ten minutes roll over and begin refilling the buffer
  if (ptr10mGust > GUST_10M_ARRAY_DEPTH - 1) {
    ptr10mGust = 0;
  }
  
  // clear the current wind gust array element
  arrWindGustSpeed10m[ptr10mGust] = 0;
}

void update1hourRainFall() {
  /* 
   * Updates the rain fall value for the current minute.  Sixty minutes of
   * data kept in order to calculate the total rain fall for the last hour.
   * Note that the rain fall sensor is calibrated to produce a switch closure
   * (or tick) for every 0.011 inches of rain fall.  Hence rain fall per
   * minute is given by the number of ticks per minute times 0.011, and the
   * rain fall per hour is that number multiplied by sixty.
   * 
   * Parameters: none
   * Returns nothing.
   */
  arrRainPerHour[ptr1hRain] = irqRainClicks;
  irqRainClicks = 0;
  
  // update 1 hour rain fall array pointer
  ptr1hRain++;
  // after two minutes roll over and begin refilling the buffer
  if (ptr1hRain > RAIN_1H_ARRAY_DEPTH - 1) { 
    ptr1hRain = 0;
  }
}

void calcWeather() {
  /*
   * Always called before reporting the weather to the destination server,
   * this routine computes weather data items.  In summary, the following
   * items get calculated:
   *     - two minute average wind speed and direction
   *     - the maximum wind gust in the last ten minutes
   *     - the rainfall for the last one hour
   *     - humidity (from humidity sensor)
   *     - temperature (from pressure sensor)
   *     - pressure (from pressure sensor)
   *     - ambient light (from light sensor)
   *     - battery voltage
   *     
   * Parameters: none
   * Returns nothing.
   */
  float xAccumulator;
  float yAccumulator;
  float angle;
  byte i;

  /*
   * To calculate the two minute average wind direction, the wind direction
   * must first be transformed from compass points to rectangular (x,y) 
   * coordinates.  To transform to rectangular coordinates the compass point 
   * must first be changed to radians and then, in order to make the y 
   * coordinate correspond to north, subtracted from PI/2.  The conversion 
   * process can best be understood by considering the formula
   *     angle = PI / 2 - compassPoint * PI / 8
   * where compass points are expressed as integers (0 for N, 1 for NNE, 
   * 2 for NE, 3 for ENE, etc). One compass point represents PI / 8
   * radians (2 x PI radians divided by 16 compass points).  For computing 
   * purposes the following formula provides a good approximation
   *     angle = 1.570796 - compassPoint * 0.3926991
   * The rectangular components are then given by
   *     x = cos(angle)
   *     y = sin(angle)
   * and the mean or average by the usual computation
   *     average(x) = sum(x1, x2,...,xn) / n
   *     average(y) = sum(y1, y2,...,yn) / n
   * 
   * Once these averages are calculated, the result must be converted back
   * to polar coordinates by using the arc tangent function. Consider
   * the following formula
   *     angle = atan2(xComponentAverage, yComponentAverage)
   * where angle is in radians. Converting angle back to compass points
   *     compassPoint = angle * 8 / PI
   * and a good approximation given by
   *     compassPoint = atan2(xAccumulator, yAccumulator) * 2.546479
   * Finally, since the atan2 function gives a negative result for left half
   * plane coordinates, subtract negative results from 16 to get the actual
   * compass point.
   */

  // calculate average of rectangular components of wind direction
  xAccumulator = 0;
  yAccumulator = 0;
  for(i = 0; i < WIND_2M_ARRAY_DEPTH; i++) {
    angle = 1.570796 - float(arrWindDirection2mAv[i]) * 0.3926991;
    xAccumulator += cos(angle);
    yAccumulator += sin(angle);
  }
  xAccumulator /= float(WIND_2M_ARRAY_DEPTH);
  yAccumulator /= float(WIND_2M_ARRAY_DEPTH);

  // convert the averages of the rectangular components back to compass point
  angle = atan2(xAccumulator, yAccumulator) * 2.546479;
  if (angle < 0) {
    angle += 16.;
  }
  windDirection2mAv = byte(angle);

  // calculate 2 minute average wind speed
  xAccumulator = 0;
  for(i = 0; i < WIND_2M_ARRAY_DEPTH; i++) {
    xAccumulator += float(arrWindSpeed2mAv[i]);
  }  
  windSpeed2mAv = byte(xAccumulator / float(WIND_2M_ARRAY_DEPTH));

  // calculate highest wind gust in last 10 minutes
  windGustSpeedMax10m = 0;
  for(i = 0; i < GUST_10M_ARRAY_DEPTH; i++) {
    if (arrWindGustSpeed10m[i] > windGustSpeedMax10m) {
      windGustSpeedMax10m = arrWindGustSpeed10m[i];
      windGustDirectionMax10m = arrWindGustDirection10m[i];
    }
  }

  // calculate  rainfall for the last 60 minutes
  xAccumulator = 0;
  for(i = 0; i < RAIN_1H_ARRAY_DEPTH; i++) {
    xAccumulator += float(arrRainPerHour[i]);
  }
  rainPerHour = xAccumulator * RAIN_CLICK_CONVERSION_FACTOR;
  rainDayTotal = float(irqTotalRainClicks) * RAIN_CLICK_CONVERSION_FACTOR;

  // get readings from sensors
  humidity = aHumiditySensor.readHumidity();
  tempf = aPressureSensor.readTempF();
  pressure = aPressureSensor.readPressure();
  lightLevel = getLightLevel();
  batteryLevel = getBatteryLevel();
}

byte getWindSpeed() {
  /*
   * Calculates the wind speed from raw sensor data.
   * For each rotation of the anemometer two switch closures (or ticks) occur. 
   * The anemometer is calibrated for 2.984 mph per rotation, or 1.492 mph
   * per switch closure or tick.  Hence wind speed in miles per hour (mph) is
   * given by number of ticks per second times 1.492.
   * 
   * Parameters: none
   * Returns the wind speed as a floating point number
   */
  unsigned long currentTime;
  float clicksPerSecond;

  currentTime = millis();
    
  // calculate the number of wind clicks per second
  clicksPerSecond = 1000.0 * float(irqWindClicks) /
                    float(abs(currentTime - lastWindCheckTime));

  // reset the wind click counter and update wind check time
  irqWindClicks = 0;
  lastWindCheckTime = currentTime;

  // calcualte and return the wind speed
  return byte(clicksPerSecond * WIND_CLICK_CONVERSION_FACTOR);
}

byte getWindDirection() {
  /*
   * Calculates the wind direction from raw sensor data.  The wind direction
   * sensor measures to an accuracy of +/- 11.25 degrees, or of one compass
   * point.  Hence, the raw data from the sensor gets converted to one of 16
   * compass points expressed by an integer (0, 1, ... 15).
   *     0 for N
   *     1 for NNE
   *     ...
   *     15 for NNW
   * 
   * Parameters: none
   * Returns the wind direction as a single byte unsigned integer.
   */
  unsigned int adc;
 
  // get the current reading from the sensor
  adc = averageAnalogRead(WIND_DIRECTION);

  // this helps to determine if the wind direction sensor is working properly
  #ifdef DEBUG
    Serial.print(F(" wdraw "));Serial.println(adc);
  #endif
  /*
   * The following are the decision points for each of 16 possible directions
   * reported by the wind diretion sensor.  Each decision point is half way
   * between the ADC reading for the two adjacent wind directions.  Note that
   * the wind directions reported by the wind sensor are not in the same order
   * as the decision points.  See the wind sensor documentation for details.
  */
  if (adc < 378) return (13);
  if (adc < 392) return (11);
  if (adc < 412) return (12);
  if (adc < 454) return (15);
  if (adc < 506) return (14);
  if (adc < 550) return (1);
  if (adc < 614) return (0);
  if (adc < 680) return (9);
  if (adc < 746) return (10);
  if (adc < 801) return (3);
  if (adc < 833) return (2);
  if (adc < 878) return (7);
  if (adc < 913) return (8);
  if (adc < 940) return (5);
  if (adc < 970) return (6);
  if (adc < 1004) return (4);
  /*
     A disconnected or defective wind sensor results in a ADC reading
     of between 1004 and 1023. In this case return 16 as to indicate
     an exception.
  */
  return (16);
}

float getLightLevel() {
/* 
 *  Calculates the voltage of the light sensor based on 3.3 volts, thus
 *  allowing for a VCC of either 3.3 or 5.0 volts.  (An Arduino plugged into
 *  a USB port has a VCC of 4.5 to 5.2V).
 *  
 *  Parameters: none
 *  Returns the voltage on the light level as a floating point number.
 */
  float lightSensorVoltage;
  float referenceVoltage;

  referenceVoltage = averageAnalogRead(REFERENCE_3V);
  lightSensorVoltage = averageAnalogRead(LIGHT_LEVEL);

  //The reference voltage is 3.25V
  referenceVoltage = 3.25 / referenceVoltage;
  lightSensorVoltage = referenceVoltage * lightSensorVoltage;

  return lightSensorVoltage;
}

float getBatteryLevel() {
  /*
   * Calculates the power supply voltage based on the 3.3 volts, thus 
   * allowing for a VCC of either 3.3 or 5.0 volts. The power supply voltage 
   * is applied to a voltage divider consisting of two 5% resistors: 3.9K on 
   * the high side (R7), and 1K on the low side (R8).  The Arduino reads the 
   * voltage on the low side via a gpio analog input (pin A2).
   * 
   * Parameters: none
   * Returns the power supply voltage level as a floating point number.
   */
  float batteryVoltage;
  float referenceVoltage;

  // get ADC readings
  referenceVoltage = averageAnalogRead(REFERENCE_3V);
  batteryVoltage = averageAnalogRead(BATTERY_LEVEL);
  
  // normalize the reference voltage ADC reading to 3.25 volts
  referenceVoltage = 3.25 / referenceVoltage;
  // normalize battery voltage ADC reading to the reference voltage
  batteryVoltage = batteryVoltage * referenceVoltage;
 
  /*   
   * Calculate the actual battery voltage across the voltage divider
   * v = (R7 + R8) / R7 = (3.9K + 1.0K)/1.0K = 4.9 times normalized battery
   * voltage plus 0.6 correction added to get true battery voltage
   */
  batteryVoltage = batteryVoltage * 4.9 + 0.6 ;
 
  return batteryVoltage;
}

void listenForClients() {

  /*   
   * Get ESP8266Client object for reading data from and writing data
   * to the requesting http client.  The function available() has one 
   * parameter which sets the number of milliseconds the function waits,
   * checking for a connection.
   */

  if(abs(int(millis() / 1000) - prevClientRequestTime) > HTTP_NO_REQUEST_TIMEOUT) {
    // http requests not getting through so reset wifi interface
    Serial.println(F("\nclient request timeout: rebooting..."));
    delay(200);

    softwareReset();
    /*  
    //connect to the stored wifi access point
    connectWifiAccessPoint();

    //display wifi connection information
    displayWifiConnection();

    // start HTTP server
    server.begin();
 
    prevClientRequestTime = int(millis() / 1000);
    */
    return;
  }

  ESP8266Client client = server.available(HTTP_CONNECTION_TIMEOUT);

  if (client) 
  {
    char sBuf[REPORT_STRING_BUFFER_LENGTH];
    byte i;
    char c, c_prev;
    boolean processedCommand;
    boolean firstLineFound;

    Serial.println(F("\nclient request"));

    i = 0;
    c_prev = 0;
    sBuf[0] = 0;
    processedCommand = false;
    firstLineFound = false;
    prevClientRequestTime = int(millis() / 1000);
 
    /*
     * The beginning and end of an HTTP client request is always signaled
     * by a blank line, that is, by two consecutive line feed and carriage 
     * return characters "\r\n\r\n".  The following lines of code 
     * look for this condition, as well as the url extension (following
     * "GET").
     */
    
    while (client.connected())  {
      if (client.available()) {
        c = client.read();

        #ifdef DEBUG
          Serial.write(c);
        #endif
              
        if (c == '\r') {
          continue; // discard character
        }  
        else if (c == '\n') {
          if (firstLineFound && c_prev == '\n') {
             break;
          }
        } 
        
        if (!processedCommand) {
          
          if (c != '\n') {
            if(i > REPORT_STRING_BUFFER_LENGTH - 2) {
              i = 0;
              sBuf[0] = 0;
            }
            sBuf[i++] = c;
            sBuf[i] = 0;
          }

          if (!firstLineFound && strstr(sBuf, "GET /") != NULL) {
            firstLineFound = true;
            strcpy(sBuf, "/");
            i = 1;
          }

          if (firstLineFound && (c == '\n' || i > REPORT_STRING_BUFFER_LENGTH - 2)) {
            processedCommand = true;
          }
        }
        c_prev = c;
      } // end single character processing
    } // end character processing loop
    
    i = processCommand(sBuf);

    sReadEeprom(sBuf, HTTP_HEADER);
    client.print(sBuf);

    switch (i) {
      case 1:
      case 2:
        client.print("ok\n");
        break;
      case 5:
        client.print("error\n");
        break;
      case 0:
        calcWeather();
    
        char sValue[10];
        char sTmp[12];
    
        sprintf(sTmp, "$,ws=%d,", windSpeed);
        strcpy(sBuf, sTmp);
       
        sprintf(sTmp, "wd=%d,", windDirection);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "ws2=%d,", windSpeed2mAv);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "wd2=%d,", windDirection2mAv);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "gs=%d,", windGustSpeed);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "gd=%d,", windGustDirection);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "gs10=%d,", windGustSpeedMax10m);
        strcat(sBuf, sTmp);
      
        sprintf(sTmp, "gd10=%d,", windGustDirectionMax10m);
        strcat(sBuf, sTmp);
      
        client.print(sBuf);
        Serial.print(sBuf);
          
        dtostrf(humidity, 1, 1, sValue);
        sprintf(sTmp, "h=%s,", sValue);
        strcpy(sBuf, sTmp);
      
        dtostrf(tempf, 1, 1, sValue);
        sprintf(sTmp, "t=%s,", sValue);
        strcat(sBuf, sTmp);
      
        dtostrf(pressure, 1, 1, sValue);
        sprintf(sTmp, "p=%s,", sValue);
        strcat(sBuf, sTmp);
      
        dtostrf(rainPerHour, 1, 2, sValue);
        sprintf(sTmp, "r=%s,", sValue);
        strcat(sBuf, sTmp);
      
        dtostrf(rainDayTotal, 1, 2, sValue);
        sprintf(sTmp, "dr=%s,", sValue);
        strcat(sBuf, sTmp);
      
        dtostrf(batteryLevel, 1, 2, sValue);
        sprintf(sTmp, "b=%s,", sValue);
        strcat(sBuf, sTmp);
      
        dtostrf(lightLevel, 1, 1, sValue);
        sprintf(sTmp, "l=%s,#\n", sValue);
        strcat(sBuf, sTmp);
      
        client.print(sBuf);
        Serial.print(sBuf);
        break;
    }

    // give the web browser time to receive the data
    delay(10);
    // close the connection:
    client.stop();
    //Serial.print(F("client disconnected\n\n"));

    if (i == 2) {
      delay(200);
      softwareReset();
    }
  } //end if client
}

byte processCommand(char * sBuf) {
  /*
   * Checks the url extension for maintenance  commands and executes
   * a commend, if present.  Otherwise, if there is no url extension,
   * then the client request is for weather data.
   * 
   * Url format
   * 
   * {ip adress}/{station password}/{command}/{parameter}
   * 
   * Commands are
   *    p - change station password to {parameter}
   *    s - change wifi ssid to {parameter}
   *    w - change wifi password to {parameter}
   *    r - reset weather station (parameter ignored)
   */
  char * pStr;
  char sTmp[17];

  // Parse url extension
  pStr = strtok(sBuf, " ");
  pStr = strtok(sBuf, "/");
  
  // A non-extended url indicates a client request for weather data
  if(pStr == NULL)
    return 0;

  // Authenticate station password
  if (strlen(pStr) > 8)
    return 5;
  sReadEeprom(sTmp, STATION_PWD);
  if (strcmp(pStr, sTmp) != 0)
    return 5;
  Serial.println(F("authenticated"));

  // Get maintenance command
  pStr = strtok(NULL, "/");
  if (strlen(pStr) > 1)
    return 5;

  switch(*(pStr + 0)) {
    // change station password
    case 'p': // change station password
      pStr = strtok(NULL, "/");
      // perform bounds check: station PIN must be 8 charachers or less
      if (pStr == NULL || strlen(pStr) > 8)
        return 5;
      sWriteEeprom(pStr, STATION_PWD);
      Serial.print(F("changing station password to: ")); Serial.println(pStr);
      return 1;

    // change wifi ssid
    case 's': // change wifi ssid
      pStr = strtok(NULL, "/");
      // perform bounds check: SSID must be 32 charachers or less
      if (pStr == NULL || strlen(pStr) > 32)
        return 5;
      //sWriteEeprom(pStr, SS_ID);
      Serial.print(F("changing ssid to: ")); Serial.println(pStr);
      return 1;

    // change wifi wpa password
    case 'w': 
      pStr = strtok(NULL, "/");
      // perform bounds check: WPA password must be 64 characters or less
      if (pStr == NULL || strlen(pStr) > 64)
        return 5;
      //sWriteEeprom(pStr, WPA_PASSWD);
      Serial.print(F("changing wpa password to: ")); Serial.println(pStr);
      return 1;
      
    // reset station
    case 'r': 
      Serial.println(F("reseting station..."));
      return 2;
  } //end switch
  return 5;
}

int averageAnalogRead(int inputPin) {
  /*
   * Calculates an average from eight samplings of the analog gpio
   * input specified by the supplied parameter.
   * 
   * Parameters:
   *   inputPin - the analog input gpio pin
   * Returns the average of the readings.
   */
  byte i;
  unsigned int accumulator;
  
  accumulator = 0; 

  for(i = 0 ; i < 8 ; i++) {
    accumulator += analogRead(inputPin);
  }
  return accumulator / 8;
}

void initializeWifiAdapter()
{
  /*
   * Initializes the ESP8266 wifi adapter.
   * 
   * Parameters: none
   * Returns nothing.
  */
  int result;
  
  result = esp8266.begin();
  /*
   * esp8266.begin() sets up the wifi adapter and returns 
   *     false, if the wifi adapter is not available 
   *     true, if operating correctly.
   */
  if (result == false) {
    Serial.println(F("error connecting to wifi adapter"));
    delay(SOFT_REBOOT_WAIT_TIME);
    softwareReset();
  }
  Serial.println(F("wifi adapter found"));
}

void connectWifiAccessPoint() {
  /*
   * Connects to the wifi access point stored in EEPROM.
   * 
   * Parameters: none
   * Returns nothing
   */
  char ssid[33];
  char wpaPasswd[65];
  int result;

  /*
   * Set the wifi adapter to station mode.
   *
   * esp8266.getMode() gets the current mode of wifi adapter.
   * This function returs one of the following three codes,
   * and the ESP8266 can be set to one of three modes by these
   * codes
   *     1, ESP8266_MODE_STA, station only
   *     2, ESP8266_MODE_AP, access point only
   *     3, ESP8266_MODE_STAAP, station/AP combo
   */
  result = esp8266.getMode();
  
  if(result != ESP8266_MODE_STA) {
    result = esp8266.setMode(ESP8266_MODE_STA);
    if (result < 0) {
      Serial.println(F("error setting wifi to station mode"));
      delay(SOFT_REBOOT_WAIT_TIME);
      softwareReset();
    }
  }
  
  sReadEeprom(ssid, SS_ID);
  sReadEeprom(wpaPasswd, WPA_PASSWD);

/*      
 * If the SOFT_CONNECT option is defined, then the code will only reset
 * the wifi connection if disconnected.  Otherwise the wifi connection
 * will be reset everytime this function gets called.
 */
#ifdef SOFT_CONNECT
  /*
   * esp8266.status() indicates the ESP8266's wifi connect
   * status.  Returns 
   *      1, when already connected to a wifi access point
   *      0, when disconnected
   *      value < 0, when communication errors occur.
   */
  result = esp8266.status();
  if (result <= 0) {
    /* 
     * esp8266.connect([ssid], [psk]) connects the wifi interface to
     * the wifi network access point. Returns
     *     -1, when the connection attempt times out (default 30 seconds)
     *     -3, when cannot connect to wifi access point
     *     value > 0, when successful.
     */
    result = esp8266.connect(ssid, wpaPasswd);
    if (result < 0) {
        Serial.print(F("error connecting to "));
        Serial.println(ssid);
        delay(SOFT_REBOOT_WAIT_TIME);
        softwareReset();
    }
  }
#else
  /* 
   * esp8266.connect([ssid], [psk]) connects the wifi interface to
   * the wifi network access point. Returns
   *     -1, when the connection attempt times out (default 30 seconds)
   *     -3, when cannot connect to wifi access point
   *     value > 0, when successful.
   */
  result = esp8266.connect(ssid, wpaPasswd);
  if (result < 0) {
        Serial.print(F("error connecting to "));
        Serial.println(ssid);
      delay(SOFT_REBOOT_WAIT_TIME);
      softwareReset();
  }  
#endif
}

void displayWifiConnection()
{
  /*
   * Display wifi connection information including the DHCP assigned
   * local IP address.
   * 
   * Parameters: none
   * Returns nothing.
   */
  char connectedSsid[33];
  int result;
  IPAddress deviceIP;
  
  /*
   * Get wifi connection information.  Note that esp8266.getAP() 
   * returns a negative integer if unsuccessful.  The connected 
   * AP is returned by reference as a parameter.
  */
  memset(connectedSsid, 0, 33);
  result = esp8266.getAP(connectedSsid);
   
  if (result > 0) {
    Serial.print(F("connected to: "));
    Serial.println(connectedSsid);
    /*
     * Also display the device's local IP address.
     */
    deviceIP = esp8266.localIP();
    Serial.print(F("device IP: ")); Serial.println(deviceIP);
    Serial.println();
  }
}

void sReadEeprom(char * sBuffer, int iLocation) {
  /*
   * Reads a data item from EEPROM. The data item is presumed
   * to be a null terminated string.
   * 
   * Parameters: 
   *   sBuffer - a character buffer which will receive the item read 
   *             from EEPROM. The caller is responsible for assuring that
   *             the buffer has sufficient size
   *   iLocation - an integer pointing to the location in EEPROM where the
   *             desire data item is stored
   * Returns nothing.
   */
  byte i;
  char c;

  i = 0;
  while(1) {
    c = EEPROM.read(iLocation + i);
    sBuffer[i] = c;
    if (c == 0) break;
    i++;
  }
}

void sWriteEeprom( char * sBuffer, int iLocation) {
  /*
   * Writes a data item to EEPROM.  The data item is presumed to be a
   * null terminated string.
   * 
   * Parameters:
   *   sBuffer - a character buffer which contains the data item to be
   *             written to EEPROM.  The caller is responsible for assuring
   *             that data item will fit in the allocated space in EEPROM.
   *   iLocation - an integer pointing to the location in EEPROM where the
   *             desired data item is to be written
   * Returns nothing.
   */
  byte i;
  char c;

  i = 0;
  while(1) {
    c = sBuffer[i];
    EEPROM.write(iLocation + i, c);
    if (c == 0) break;
    i++;
  }
}

void softwareReset() {
  /*
   * Restarts the Uno and runs this program from beginning.
   * 
   * Parameters: none
   * Returns nothing.
  */
  asm volatile ("  jmp 0");
}  

#ifdef DEBUG
void displayArrays(byte sel) {
  /*
   * Displays arrays for debugging purposes.
   * Parameters: 
   *   sel - Can be one of the following
   *         's' - display 2 minute average wind array
   *         'm' - display 10 minute wind gust array
   *         'h' - display 60 minute rain fall array
   * Returns nothing.
   */
  byte i;

  switch (sel) {
    case 's':
      Serial.print(F("2m wind: "));
      for(i = 0; i < WIND_2M_ARRAY_DEPTH; i++) {
        Serial.print(arrWindSpeed2mAv[i]); Serial.print(F(", "));
      }
      Serial.println();
      break;
    case 'm':
      Serial.print(F("10m gust: "));
      for(i = 0; i < 10; i++) {
        Serial.print(arrWindGustSpeed10m[i]); Serial.print(F(", "));
      }
      Serial.println();
      break;
    case 'h':
      Serial.print(F("1h rain: "));
      for(i = 0; i < RAIN_1H_ARRAY_DEPTH; i++) {
        Serial.print(arrRainPerHour[i]); Serial.print(F(", "));
      }
      Serial.println();
      break;
  }
}
#endif
