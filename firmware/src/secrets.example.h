// Copy this file to `secrets.h` in the same folder and fill in your own values.
// `secrets.h` is gitignored, so your credentials never reach the repository.
//
// SERVER_HOST is the LAN IP of the PC running app.py - not "localhost", which on
// the ESP32 means the ESP32 itself. Find it with `ipconfig` on Windows (IPv4
// Address, e.g. 192.168.1.50). The board and the PC must be on the same network,
// and the SSID must be 2.4 GHz: the ESP32 cannot see a 5 GHz network.
#pragma once

#define WIFI_SSID    "YOUR_WIFI_NAME"
#define WIFI_PASS    "YOUR_WIFI_PASSWORD"
#define SERVER_HOST  "192.168.1.50"
#define SERVER_PORT  5000
