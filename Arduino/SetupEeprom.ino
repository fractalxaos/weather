/*  EDIT THE FOLLOWING THREE LINES  */

//#define DEST_SERVER "[your remote server URL - see instructions]"
//#define SS_ID "[your wifi access point's SSID]" 
//#define WPA_PASSWD "[your wifi access point's WPA password]"

#define REMOTE_SERVER_UPDATE_INTERVAL "10"

/*  DO NOT EDIT ANYTHING BELOW THIS LINE */

#include <EEPROM.h>
#define DEST_SERVER_ADDR 0
#define REMOTE_SERVER_UPDATE_INTERVAL_ADDR 80
#define SS_ID_ADDR 84
#define WPA_PASSWD_ADDR 120

void setup() {
  char sBuf[128];

  Serial.begin(9600);
  while(!Serial);

  strcpy(sBuf, REMOTE_SERVER_UPDATE_INTERVAL);
  if(strlen(sBuf) > 3) {
    Serial.println(F("error: remote server update interval string too long"));
  }
  Serial.print(F("writing remote server update interval to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, REMOTE_SERVER_UPDATE_INTERVAL_ADDR);

  strcpy(sBuf, DEST_SERVER);
  if(strlen(sBuf) > 66) {
    Serial.println(F("error: destination server string too long"));
  }
  Serial.print(F("writing server URL to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, DEST_SERVER_ADDR);

  strcpy(sBuf, SS_ID);
  if(strlen(sBuf) > 32) {
    Serial.println(F("error: ssid string too long"));
  }
  Serial.print(F("writing SSID to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, SS_ID_ADDR);

  strcpy(sBuf, WPA_PASSWD);
  if(strlen(sBuf) > 64) {
    Serial.println(F("error: password string too long"));
  }
  Serial.print(F("writing password to EEPROM: ")); Serial.println(sBuf);
  sWriteEeprom(sBuf, WPA_PASSWD_ADDR); 
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


