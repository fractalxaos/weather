#!/bin/sh
#
# Create directories in tmpfs for dynamic data.  Bind the directories
# to their respective paths in the public_html directory.  This assures
# that dynamic content gets written to ram.

# Create a directory in tmpfs for weather dynamic data.  Set group
# permissions so that apache2 can read and write
# to this directory.
mkdir /tmp/weather
sudo chown :www-data /tmp/weather
chmod g+w /tmp/weather
# Start weather station device agent
(sleep 10; /home/pi/bin/weastart;) &

# Blink LED to show boot process complete
#sudo /home/pi/bin/blinkLed.sh
