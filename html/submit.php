<?php

/* 
   Authenticate that the client (weather station) is on the local area
   network.  This addresses a potential security issue that a malicious
   client outside the network could inject bad data into the input data
   file.
*/
//$aIp = "73.157.139.23";
//$aIp = "192.168.1.26";
$aIp = $_SERVER["SERVER_ADDR"];

$cIp = explode(".", $_SERVER["REMOTE_ADDR"]);
$sIp = explode(".", $aIp);
for($inx = 0; $inx < count($cIp) - 1; $inx++) {
    if( $cIp[$inx] != $sIp[$inx] ) {
        die ("unauthorized client\n");
    }
}

// Get path to weather station input data file
define("_WEATHER_INPUT_DATA_FILE", str_replace("submit.php",
                                     "dynamic/weatherInputData.js",
                                     $_SERVER["SCRIPT_FILENAME"]));

// Get path to weather station maintenance signal file
define("_MAINTENANCE_SIGNAL_FILE", str_replace("submit.php", "maintsig",
                                     $_SERVER["SCRIPT_FILENAME"]));

function getMaintenanceSignal() {
    /*
     * Check the weather maintenance signal file for a signal. A valid signal
     * is something other than "!ok".  If a valid signal is found, then that
     * signal gets passed to the weather station, and the signal gets reset.
     * The signal gets reset by overwriting with "!ok" the signal in the
     * weather maintenance file.
     */
    // Read the signal from the weather maintenance file.
    $fp = fopen(_MAINTENANCE_SIGNAL_FILE, "r") or
                die("submit failed: cannot open sig file for read\n");
    $maintenanceSignal = fgets($fp);
    fclose($fp);

    // Overwrite the signal with "!ok" if not "!ok".
    $pos = strpos($maintenanceSignal, "!ok");
    if ($pos === false || $pos > 0) { 
      $fp = fopen(_MAINTENANCE_SIGNAL_FILE, "w") or
                  die("submit failed: cannot open sig file for write\n");
      fwrite($fp, "!ok\r\n");
      fclose($fp);
    }
    return $maintenanceSignal;
}

function writeInputFile($weatherData) {
    /*
     * Write to a file the weather data included in the GET request from the
     * weather station.  This data is formatted into a JSON object before
     * writing to the file.
     */
    $timeStamp = date("m/d/Y H:i:s");
    $fp = fopen(_WEATHER_INPUT_DATA_FILE, "w") or
                die("submit failed: cannot open input data file for write\n");
    $weatherInputData =
                "[{\"date\":\"$timeStamp\",\"weather\":\"$weatherData\"}]\n";
    fwrite($fp, $weatherInputData);
    fclose($fp);

    return;
}

// get data from weather station
$weatherData = $_GET['wea'];

// write data to JSON file
writeInputFile($weatherData);

// send the maintenance signal back to the weather station
echo getMaintenanceSignal();

?>
