# DIY-Stream-Deck

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Platform: ESP32](https://img.shields.io/badge/Platform-ESP32-red.svg)  
![Language: C++](https://img.shields.io/badge/Language-C++-blue.svg)
![Python: 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)

A compact physical audio mixer to control individual application volumes on a Windows PC using rotary encoders and an ESP32 microcontroller. Supports real-time volume adjustment, mute toggling, and automatic channel reset when applications close.

## Features

- Controls up to 3 independent audio channels  
- Real-time volume control and mute/unmute per channel  
- Automatic reset of channel volume when applications close  
- Application priority mapping  
- Serial communication between Windows PC (Python script) and ESP32    

## Components

- ESP32-S3 DevKitC  
- 3x EC11 rotary encoders with push buttons  
- 3x GC9D01 LCD's display (from aliexpress)

## Included in This Repository

- STL files for 3D printing the case and encoder knobs   
- Python script for Windows PC volume management  
- Arduino `.ino` sketch for ESP32 firmware

## Future Plans

- Optimize and improve the current functionality for smoother performance  
- Enable the functionality of the top buttons, which are currently non-functional  
- Add icons on the displays to show mute status instead of just “0”  
- Display the icon of the currently open application on the mixer’s display  
- Redesign the 3D model for the case to enhance aesthetics and usability  
- Electrical connection schematic  

![DIY Stream Deck](diy-stream-deck.png)
