/*  EDIT THE FOLLOWING THREE LINES  */

//#define STATION_PASSWORD "{weather station password (required)}"
//#define SS_ID "{your wifi access point's SSID (required)}" 
//#define WPA_PASSWD "{your wifi access point's WPA password (required)}"

#define STATION_PASSWORD "12345"
#define SS_ID "{your wifi ssid}" 
#define WPA_PASSWD "{your wifi password}"

/*  DO NOT EDIT ANYTHING BELOW THIS LINE */

#include <EEPROM.h>
#define STATION_PASSWORD_ADDR 0
#define SSID_ADDR 18
#define WPA_PASSWD_ADDR 52
#define HTTP_HEADER_ADDR 120 

#define HTTP_HEADER "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnnection: close\r\n\r\n"

void setup() {
  char sBuf[128];

  Serial.begin(9600);
  while(!Serial);

  strcpy(sBuf, STATION_PASSWORD);
  if(strlen(sBuf) > 16) {
    Serial.println(F("error: station password too long"));
  }
  Serial.print(F("writing station password to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, STATION_PASSWORD_ADDR);

  strcpy(sBuf, SS_ID);
  if(strlen(sBuf) > 32) {
    Serial.println(F("error: ssid string too long"));
  }
  Serial.print(F("writing SSID to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, SSID_ADDR);

  strcpy(sBuf, WPA_PASSWD);
  if(strlen(sBuf) > 64) {
    Serial.println(F("error: password string too long"));
  }
  Serial.print(F("writing password to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, WPA_PASSWD_ADDR);

  strcpy(sBuf, HTTP_HEADER);
  Serial.print(F("writing http header to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, HTTP_HEADER_ADDR);
}

void loop() {
}

void sWriteEeprom( char * sBuffer, int iLocation) {
  int i;
  char c;

  i = 0;
  while(1) {
    c = sBuffer[i];
    EEPROM.write(iLocation + i, c);
    if (c == 0) break;
    i++;
  }
}
