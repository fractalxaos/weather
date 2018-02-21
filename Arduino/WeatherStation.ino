/*
 DIY Weather Station
  
 Copyright 2015 Jeff Owrey
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
   * v1.0 released 17 Sept 2015 by J L Owrey: first release
   * v1.1 released 21 Dec 2015 by J L Owrey: bug fixes, utilize ESP8266
          wifi chip deep sleep mode, report ambient light
   * v1.2 released 04 Jan 2016 by J L Owrey: improved algorithm for
          calculating average 2 minute wind speed and direction; report
          all wind directions as compass points (0 thru 15); bug fixes
   * v2.0 released 11 Nov 2017 by J L Owrey: bug fixes, disable put wifi to
          sleep feature as this is not needed, fixed light level and
          battery level functions; fix wifi issues; upgrade http client
          request to work with apache 2.4 or greater.
   * v2.1 released 06 Feb 2018 by J L Owrey: added feature to include
          destination server tcp port as part of destination server url
   * v2.2 released 07 Feb 2018 by J L Owrey: added feature to remotely
          change the remote server update interval
*/

/***  PREPROCESSOR DEFINES  ***/

//#define DEBUG
//#define USE_SLEEP_FUNCTION
//#define SOFT_CONNECT

/*
 * System configuration defines
 */
#define MAX_CONNECT_ATTEMPTS 12
#define WIFI_CONNECT_RESET_TIME 10000

/*
 * Includes
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
#define DESTINATION_URL 0
#define REMOTE_SERVER_UPDATE_INTERVAL 80
#define SS_ID 84
#define WPA_PASSWD 120

// Array depths
#define WIND_2M_ARRAY_DEPTH 120
#define RAIN_1H_ARRAY_DEPTH 60
#define GUST_10M_ARRAY_DEPTH 10
#define REPORT_STRING_BUFFER_LENGTH 82

// Constants
#define WIND_SENSOR_DEBOUNCE_SETTLING_TIME 10 // milliseconds
#define RAIN_SENSOR_DEBOUNCE_SETTLING_TIME 50 // milliseconds
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

// I2C sensors
MPL3115A2 aPressureSensor; //Create an instance of the pressure sensor
HTU21D aHumiditySensor; //Create an instance of the humidity sensor

// Interrupt Handlers
// rain
volatile byte irqRainClicks;
volatile unsigned int irqTotalRainClicks;
volatile unsigned long lastRainClickTime;
// wind
volatile byte irqWindClicks;
volatile unsigned long lastWindClickTime;

// Housekeeping
byte seconds;
byte minutes;
unsigned long lastLoopCycleTime;
unsigned long lastWindCalcTime;
byte ptr2mWind;
byte ptr1hRain;
byte ptr10mGust;
byte connectAttempts;
byte remoteServerUpdateInterval;

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
  char sBuffer[4];
  
  // start up USB serial port
  Serial.begin(9600);
  Serial.println(F("\nDIY weather v2.2"));
  Serial.println(F("(c) 2018 intravisions.com\n"));
  
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

  // get remote server update interval
  sReadEeprom(sBuffer, REMOTE_SERVER_UPDATE_INTERVAL);
  remoteServerUpdateInterval = atoi(sBuffer);

  // initialize global variables used by interrupt handlers
  irqRainClicks = 0;
  irqTotalRainClicks = 0;
  lastRainClickTime = 0;
  irqWindClicks = 0;
  lastWindClickTime = 0;
  connectAttempts = 0;

  // turn on interrupt handlers
  attachInterrupt(1, WindSpeedIRQ, FALLING);
  attachInterrupt(0, RainFallIRQ, FALLING);

  lastLoopCycleTime = millis();
  batteryLevel = getBatteryLevel();
  delay(10);
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

    // this helps determine the load on the micro-controller
    #ifdef DEBUG
      Serial.print(F("time: "));Serial.println(millis() - lastLoopCycleTime);
      Serial.println();
    #endif
  }
}

void doEverySecond() {
  /*
   * This routine triggers events that happen with periodicities defined in
   * intervals of seconds.  In summary, the following events get triggered:
   *     - update the wind gust array with latest wind gust (if any)
   *     - update the 2 minute wind array (used for calculating an average
   *       value of wind over the last 2 minutes)
   *     - send the weather report to the remote server
   *     
   * Parameters: none
   * Returns nothing.
   */
   
  // this helps to determine if wind and rain sensors are working properly
  #ifdef DEBUG
    Serial.print(F("rain clicks: ")); Serial.println(irqTotalRainClicks);
    Serial.print(F("wind clicks: "));Serial.println(irqWindClicks);
    Serial.print(F("wind dir: "));Serial.println(windDirection);
  #endif

  // get instantanious wind speed and direction  
  windSpeed = getWindSpeed();
  windDirection = getWindDirection();
  
  // update 2 minute wind average
  if (seconds % (120 / WIND_2M_ARRAY_DEPTH) == 0) {
    update2minWindAverage();

    // this helps to determine if data arrays are working properly
    #ifdef DEBUG
      displayArrays('s');
    #endif
  }
  
  // update daily and 10 minute maximum wind gust
  update1secWindGust();
  
  // at regular intervals calculate and report weather data to remote server
  if (seconds % remoteServerUpdateInterval == 0) {
    calcWeather();
    delay(5);
    reportWeather();
    
    #ifdef USE_SLEEP_FUNCTION
      /*
       * reportWeather gets called after the ESP8266 wifi has woken up from
       * sleep mode.  Reading the battery voltage after the wifi gets put
       * back to sleep filters out any spurious effects due to momentary
       * surges caused by the wifi transmitting data.
       */
      sendSleepCmd();
      batteryLevel = getBatteryLevel();
    #endif
  }
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
  #ifdef DEBUG
    displayArrays('m');
    displayArrays('h');
  #endif
}

void update2minWindAverage() {
  /*
   * Updates the two minute average wind speed and direction arrays. The
   * arrays contain wind speed and direction data for a two minute period
   * ending at the current time.  This data is used by calcWeather to 
   * compute the average wind speed and direction for the previous 120
   * seconds.  The two minute average is often referred to as "Sustained
   * Wind".
   * 
   * Parameters: none
   * Returns nothing.
   */
  arrWindSpeed2mAv[ptr2mWind] = windSpeed;
  arrWindDirection2mAv[ptr2mWind] = windDirection;
  /*
    Update the 2 minute array pointer.  This implements a 
    round-robin FIFO memory structure with the pointer always pointing
    to the first element in. Reset the pointer back to the beginning of
    the array when it reaches the end of the array.
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
   *   compassPoint = atan2(xAccumulator, yAccumulator) * 2.546479
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
                    float(abs(currentTime - lastWindCalcTime));

  // reset the wind click counter and update wind calculation time
  irqWindClicks = 0;
  lastWindCalcTime = currentTime;

  // calculate and return the wind speed
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
    Serial.print(F("wdraw: "));Serial.println(adc);
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

void reportWeather() {
  /*
   * Sends weather data to the remote server.  The HTTP GET method is used 
   * to transmit the weather data, embedded in the URL.  Note that this
   * function has not been broken up into smaller sub-functions due to
   * extreme limitations in available heap memory.
   * 
   * Parameters: none
   * Returns nothing.
   */
  char * sDestServerAddr;
  char * sDestServerPort;
  char * sUrlExtension;

  char sBuffer[REPORT_STRING_BUFFER_LENGTH];
  char sFormatBuffer[14];
  char sValue[10];
  
  char c;
  char c_prev;
  boolean startLineFound;
  boolean messageBodyFound;
  int iVar;

  #ifdef USE_SLEEP_FUNCTION
    /*
    * Due to a bug in the ESP8266 library, it is necessary to restart the
    * wifi software interface every time after coming out of deep sleep mode.
    */
    esp8266.begin();
    // do nothing if wifi AP not available
    if (esp8266.status() == 0) {
      Serial.print(F("wifi AP not available\n\n"));
      return;
    }    
  #endif

  // create ESP8266 TCP client object
  ESP8266Client client;

  /*
   * Read the destination server URL from EEPROM.  The URL
   * has two parts. The server host domain name (e.g. intravisions.com)
   * and an extension that calls a PHP script on the server side. The
   * weather data then gets passed to this PHP script by sending it to
   * the client as part of the URL.  For example, the destination server  
   * URL stored in EEPROM is "intravisions.com/weather/submit.php".
   * A connection to "intravisions.com" will be made.  At that point,
   * "/weather/submit.php" will be prepended to the formatted weather 
   * data, and all transmitted to the remote server as the URL.
   */
  sReadEeprom(sBuffer, DESTINATION_URL);
  sDestServerAddr = strtok(sBuffer, "/");
  if (sDestServerAddr == NULL) {
    Serial.print(F("error in destination server URL\n\n"));
    return;
  }
  sUrlExtension = strtok(NULL, "");
  if (sUrlExtension == NULL) {
    Serial.print(F("error in destination server URL extension\n\n"));
    return;
  }
  sDestServerAddr = strtok(sDestServerAddr, ":");
  if (sDestServerAddr == NULL) {
    Serial.print(F("error in destination server address\n\n"));
    return;
  }
  sDestServerPort = strtok(NULL, ":");
  if (sDestServerPort == NULL) {
    Serial.print(F("error in destination server port\n\n"));
    return;
  }
  /*
   * Connect to the remote server via the standard HTTP port (80).
   * Note that connect(SERVER, PORT) returns 
   *     1, if successful
   *     2, if already connected to the server
   *    -1, connection attempt timed out
   *    -3, connection attempt failed
   */
  iVar = client.connect(sDestServerAddr, atoi(sDestServerPort));
  if (iVar <= 0) {
    Serial.print(F("failed to connect to destination server: "));
    Serial.print(sDestServerAddr);Serial.print(F(":"));Serial.println(sDestServerPort);
    Serial.println();
    connectAttempts += 1;
    if(connectAttempts > MAX_CONNECT_ATTEMPTS - 1) {
      Serial.print(F("rebooting...\n"));
      delay(5000);
      softwareReset();
    }
    if (client.connected()) {
      client.stop(); // stop() closes a TCP connection.
    }
    return;
  }
  Serial.print(F("connected to destination server: "));
  Serial.print(sDestServerAddr);Serial.print(F(":"));Serial.println(sDestServerPort);

  delay(5);
  
  /*
   * Construct the url extension string and send to remote server.
   * The url references a php script followed by all the weather data
   * to be transmitted to the remote server.
   */
  sprintf(sBuffer, "GET /%s?wea=", sUrlExtension);
  client.print(sBuffer);

  sprintf(sFormatBuffer, "$,ws=%d,", windSpeed);
  strcpy(sBuffer, sFormatBuffer);
 
  sprintf(sFormatBuffer, "wd=%d,", windDirection);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "ws2=%d,", windSpeed2mAv);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "wd2=%d,", windDirection2mAv);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "gs=%d,", windGustSpeed);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "gd=%d,", windGustDirection);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "gs10=%d,", windGustSpeedMax10m);
  strcat(sBuffer, sFormatBuffer);

  sprintf(sFormatBuffer, "gd10=%d,", windGustDirectionMax10m);
  strcat(sBuffer, sFormatBuffer);

  client.print(sBuffer);
  Serial.print(sBuffer);
    
  dtostrf(humidity, 1, 1, sValue);
  sprintf(sFormatBuffer, "h=%s,", sValue);
  strcpy(sBuffer, sFormatBuffer);

  dtostrf(tempf, 1, 1, sValue);
  sprintf(sFormatBuffer, "t=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  dtostrf(pressure, 1, 1, sValue);
  sprintf(sFormatBuffer, "p=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  dtostrf(rainPerHour, 1, 2, sValue);
  sprintf(sFormatBuffer, "r=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  dtostrf(rainDayTotal, 1, 2, sValue);
  sprintf(sFormatBuffer, "dr=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  dtostrf(batteryLevel, 1, 2, sValue);
  sprintf(sFormatBuffer, "b=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  dtostrf(lightLevel, 1, 1, sValue);
  sprintf(sFormatBuffer, "l=%s,", sValue);
  strcat(sBuffer, sFormatBuffer);

  client.print(sBuffer); 
  Serial.println(sBuffer);

  sReadEeprom(sBuffer, DESTINATION_URL);
  sDestServerAddr = strtok(sBuffer, "/");
  sDestServerAddr = strtok(sDestServerAddr, ":");

  /* 
   *  Changed below 23 Nov 2017.
   *  Due to changes in apache 2.4.10 and greater, apache now requres lines 
   *  to be terminated by both the carriage return and newline characters.
   */
  client.print(" HTTP/1.1\r\nHost:");
  client.print(sDestServerAddr);
  client.print("\r\nConnection: close\r\n\r\n");
  sBuffer[0] = 0;

  // allow time for remote server to respond (if necessary)
  delay(10);

  /*
   * The following section of code processes the response from the remote
   * server.  The response header from the remote server begins with 
   * a blank line (\r\n\r\n) and always ends with a blank line.  The message 
   * body following the header contains the command (if any) from the remote 
   * server. The following code retains the text following the message 
   * header.
   */
  iVar = 0;
  c_prev = 0;
  messageBodyFound = false;
  startLineFound = false;
  
  while (client.available()) {

    c = client.read();

    sBuffer[iVar] = c;
    iVar++;
    // append string null terminator
    sBuffer[iVar] = 0;
    // prevent buffer overflows
    if (iVar > REPORT_STRING_BUFFER_LENGTH - 2) {
      iVar = 0;
      sBuffer[0] = 0;
    }

    if (messageBodyFound) {
      continue;
    } else  if (c == '\r') {      
      continue;  // disregard carriage return character
    } else if (c == '\n') {
      /*
       * Process new line character by checking for blank lines.  A blank 
       * line occurs at the beginning of the message header and always at 
       * the end of the header.  Check for the first printable character 
       * to determine the beginning of the message header.
       * 
       * Two new line characters in sequence (disregarding carriage returns) 
       * indicate a blank line.
       */
      #ifdef DEBUG
        Serial.print(sBuffer);
      #endif
      if (c_prev == '\n') {
        if (startLineFound) {
          // The second blank line marks the beginning of the message body.
          messageBodyFound = true;
          connectAttempts = 0;
        }
      }
      iVar = 0; // Discard message header lines.
    } else {
      // First printable character means the start line has arrived.
      startLineFound = true;
    }
    c_prev = c;
  } 
  Serial.println(sBuffer);
  
  if (client.connected()) {
    client.stop(); // stop() closes a TCP connection.
  }

  processMaintenanceCommand(sBuffer);
}

void processMaintenanceCommand( char * sBuffer) {
  /*
   * Parses message body from remoter server and executes the embedded
   * command (if any). Commands from the remote server always begin with
   * a '!' character at the beginning of the line, followed by
   *     r - forces a software reset of the local device (used to perform
   *         the midnight reset of wind and rain data)
   *     s=SSID - changes the ssid stored in eeprom to the new ssid
   *     p=PASSWWORD - changes the wpa password stored in eeprom to the new
   *                   wpa password
   *     u=URL - changes the URL of the destination server to url
   *     
   *  Parameters:
   *    sBuffer - string containing the command from the server (if any)
   *  Returns nothing.
   */
    
  char * cPtr;

  if (sBuffer[0] != '!') return;
  
  switch (sBuffer[1])
    {
    case 'r': // reset command
      Serial.println(F("reset command received: rebooting...\n"));
      delay(200);
      softwareReset();
      break;
      
    case 's': // change SSID command
      cPtr = strtok(sBuffer, "=");
      cPtr = strtok(NULL, "\r\n");
      
      // perform bounds check: SSID must be 32 charachers or less
      if (cPtr == NULL) break;
      if (strlen(cPtr) > 32) break;
      
      Serial.print(F("changing SSID to: "));Serial.println(cPtr);
      Serial.println();
      sWriteEeprom(cPtr, SS_ID);
      break;
      
    case 'p': // change WPA password command
      cPtr = strtok(sBuffer, "=");
      cPtr = strtok(NULL, "\r\n");

      // perform bounds check: WPA password must be 64 characters or less
      if (cPtr == NULL) break;
      if (strlen(cPtr) > 64) break;
      
      Serial.print(F("changing password to: "));Serial.println(cPtr);
      Serial.println();
      sWriteEeprom(cPtr, WPA_PASSWD);
      break;
      
    case 'u': // change destination server URL command
      cPtr = strtok(sBuffer, "=");
      cPtr = strtok(NULL, "\r\n");

      // perform bounds check: destination url must be 66 characters or less
      if (cPtr == NULL) break;
      if (strlen(cPtr) > 66) break;
      
      Serial.print(F("changing destination url to: "));Serial.println(cPtr);
      Serial.println();
      sWriteEeprom(cPtr, DESTINATION_URL);
      break;

   case 't': // change destination server update interval
      cPtr = strtok(sBuffer, "=");
      cPtr = strtok(NULL, "\r\n");

      // perform bounds check: update interval must be 3 characters or less
      if (cPtr == NULL) break;
      if (strlen(cPtr) > 3) break;
      
      Serial.print(F("changing server update interval to: "));Serial.println(cPtr);
      Serial.println();
      sWriteEeprom(cPtr, REMOTE_SERVER_UPDATE_INTERVAL);
      break;
  }
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
    delay(5000);
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
      delay(WIFI_CONNECT_RESET_TIME);
      softwareReset();
    }
  }
  
  sReadEeprom(ssid, SS_ID);
  sReadEeprom(wpaPasswd, WPA_PASSWD);
  /*
   * esp8266.status() indicates the ESP8266's wifi connect
   * status.  Returns 
   *      1, when the device is already connected 
   *      0, when disconnected. 
   *      value < 0, when communication errors occur
   */
#ifdef SOFT_CONNECT
  result = esp8266.status();
  if (result <= 0) {
    Serial.print(F("connecting to: "));
    Serial.println(ssid);
    /* 
     * esp8266.connect([ssid], [psk]) connects the wifi interface to
     * the wifi network access point. Returns
     *     -1, when the connection attempt times out (default 30 seconds)
     *     -3, when cannot connect to wifi access point
     *     value > 0, when successful.
     */
    result = esp8266.connect(ssid, wpaPasswd);
    if (result < 0) {
        Serial.println(F("error connecting to wifi access point"));
        delay(5000);
        softwareReset();
    }
  }
#else
  Serial.print(F("connecting to: "));
  Serial.println(ssid);
  /* 
   * esp8266.connect([ssid], [psk]) connects the wifi interface to
   * the wifi network access point. Returns
   *     -1, when the connection attempt times out (default 30 seconds)
   *     -3, when cannot connect to wifi access point
   *     value > 0, when successful.
   */
  result = esp8266.connect(ssid, wpaPasswd);
  if (result < 0) {
      Serial.println(F("error connecting to wifi access point"));
      delay(5000);
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
    Serial.print(F("weather station IP: ")); Serial.println(deviceIP);
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
   *             The data item string must be null terminated by a 0.
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

#ifdef USE_SLEEP_FUNCTION
  void sendSleepCmd() {
    /*
     * Sends the sleep command to the ESP8266 wifi interface.
     * 
     * Parameters: none
     * Returns nothing.
    */  
    char sBuf[18];
    byte i;
  
    i = 0;
    sprintf(sBuf, "AT+GSLP=%d\r\n", 1000 * 
            (REMOTE_SERVER_UPDATE_INTERVAL - 7));
    while (sBuf[i] != 0) {
      esp8266.write(sBuf[i]);
      i++;
    }
    delay(20); // required wait after sending sleep command
    esp8266.flush();
  }
#endif

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

