# TuyaRoonVolumeKnob
A way to use the Tuya Smart Knob as a Volume Control for a Roon Zone of your choice, with a web interface to control settings

NOTE: this repository is still work in progress and is not complete. Don't use it as yet, or do so at your own peril.

To achieve the described functionality we are using the following hardware:

- Raspberry Pi 4
- SMLIGHT 7 Zigbee Controller
- Tuya Smart Knob

This project and the underlying code was developed with help from DeepSeek.com and Claude.ai

To start with, we install zigbee2mqtt and Mosquitty on the Raspberry Pi.

I had some problems with the controller initially. I suspected that this had to do with the firmware. To install the latest firmware, you have to use a web based flasher (Google Chrome is needed). I could not flash from my Mac, so I had install the full desktop version of Raspbian on the Raspberry Pi, enable VNC, and access the Pi's Desktop from my Mac with the VNC Viewer. This way I was able to update the Controller's firmware. Instuctions for this are available on the web.

Important for making the Extension work:

1) After the code starts running, Enable the Extension in the Roon app.
2) Open the web interface: mytuyaknob.local:8081 (replace mytuyaknob.local with the name or IP address of the Raspberry Pi)
  
The full description of how to set up the Raspberry Pi can be found in the Wiki.
