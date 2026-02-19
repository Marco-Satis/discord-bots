#!/bin/bash
# Drop system caches safely
# This script is called via sudo from the optimizer module
# Install: sudo cp scripts/drop-caches.sh /usr/local/bin/drop-caches.sh
#          sudo chmod 755 /usr/local/bin/drop-caches.sh
sync
echo 3 > /proc/sys/vm/drop_caches
