#!/usr/bin/env python3
"""
Robust Tuya-Roon Volume Controller
A robust physical knob controller for Roon audio system using Tuya Smart Knob.

Features:
- Physical volume control with Tuya Smart Knob via Zigbee2MQTT
- Play/pause toggle with single press
- Robust connection handling with automatic retries
- Web interface with real-time status and battery monitoring
- Service mode for background operation
- Comprehensive error handling and logging

Controls:
- Rotate left/right: Volume down/up
- Single press: Play/Pause toggle
- Double press: Set volume to 50%
- Hold: Set volume to 0% (mute)

Author: Built with Claude
Version: 2.0 Final
"""

import json
import time
import threading
import signal
import sys
import configparser
from os import path
from roonapi import RoonApi
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request


class RobustTuyaRoonController:
    def __init__(self):
        self.app = Flask(__name__)
        self.roonapi = None
        self.mqtt_client = None
        self.zone_output_id = None
        self.config = self.load_config()
        self.controller_thread = None
        self.controller_running = False
        self.mqtt_connected = False
        self.roon_connection_healthy = False
        self.last_successful_command = time.time()
        self.knob_battery = None
        self.knob_last_seen = None
        self.knob_voltage = None
        self.knob_linkquality = None
        self.setup_signal_handlers()
        self.setup_routes()

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"\n🔔 Received signal {signum}, shutting down gracefully...")
        self.stop_controller()
        sys.exit(0)

    def load_config(self):
        """Load configuration from JSON file"""
        config_file = 'tuya_roon_config.json'
        
        default_config = {
            "zone_id": None,
            "zone_name": "Default",
            "volume": 15,
            "volume_step": 5,
            "mqtt_broker": "localhost",
            "mqtt_port": 1883,
            "tuya_knob_topic": "zigbee2mqtt/TuyaKnob"
        }
        
        try:
            if path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    # Merge with defaults for any missing keys
                    for key, value in default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            else:
                print(f"⚠️  Config file not found, creating default: {config_file}")
                self.save_config_dict(default_config, config_file)
                return default_config
        except Exception as e:
            print(f"❌ Error loading config: {e}")
            return default_config

    def save_config(self):
        """Save current configuration to JSON file"""
        return self.save_config_dict(self.config, 'tuya_roon_config.json')

    def save_config_dict(self, config_dict, filename):
        """Save configuration dictionary to file"""
        try:
            with open(filename, 'w') as f:
                json.dump(config_dict, f, indent=2)
            return True
        except Exception as e:
            print(f"❌ Error saving config: {e}")
            return False

    def robust_roon_command(self, command_func, max_retries=3):
        """Execute Roon command with retry logic and connection recovery"""
        for attempt in range(max_retries):
            try:
                if not self.roonapi:
                    print("⚠️  Roon API not available, attempting reconnection...")
                    if not self.setup_roon():
                        if attempt < max_retries - 1:
                            print(f"🔄 Retry {attempt + 1}/{max_retries} in 3 seconds...")
                            time.sleep(3)
                            continue
                        else:
                            print("❌ Failed to reconnect to Roon after all retries")
                            self.roon_connection_healthy = False
                            return False

                result = command_func()
                self.last_successful_command = time.time()
                self.roon_connection_healthy = True
                return result

            except Exception as e:
                print(f"⚠️  Roon command failed (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    print(f"🔄 Retrying in 2 seconds...")
                    time.sleep(2)
                    # Try to reconnect for next attempt
                    self.setup_roon()
                else:
                    print("❌ Command failed after all retries")
                    self.roon_connection_healthy = False

        return False

    def setup_roon(self):
        """Initialize Roon API connection with authorization handling"""
        try:
            print(f"🔍 Connecting to Roon Core...")
            
            config = configparser.ConfigParser()
            config.read('/usr/local/Roon/etc/roon_api.ini')
            
            server = config['DEFAULT']['RoonCoreIP']
            port = config['DEFAULT']['RoonCorePort']
            tokenfile = config['DEFAULT']['TokenFileName']
            version = config['DEFAULT']['RoonCommandLineVersion']
            release = config['DEFAULT']['RoonCommandLineRelease']
            fullver = version + "-" + release
            
            print(f"🔗 Roon Core at {server}:{port}")
            
            appinfo = {
                "extension_id": "tuya_roon_robust_controller",
                "display_name": "Tuya Knob Robust Controller",
                "display_version": fullver,
                "publisher": "TuyaRoonRobustController",
                "email": "user@example.com",
                "website": "https://github.com/user/tuya-roon-robust",
            }
            
            if path.exists(tokenfile):
                token = open(tokenfile).read()
            else:
                token = "None"
            
            # Create Roon API connection
            self.roonapi = RoonApi(appinfo, token, server, port, blocking_init=False)
            
            # Wait for authorization with user-friendly messages
            max_wait_time = 300  # 5 minutes
            check_interval = 5   # Check every 5 seconds
            waited_time = 0
            
            print("🔐 Waiting for Roon authorization...")
            
            while waited_time < max_wait_time:
                try:
                    # Test if we can access zones (indicates successful authorization)
                    zones = self.roonapi.zones
                    outputs = self.roonapi.outputs
                    
                    # Check if we actually have access to zones/outputs
                    if zones is not None and outputs is not None:
                        # Save token if successful
                        with open(tokenfile, "w") as f:
                            f.write(str(self.roonapi.token))
                        
                        self.roon_connection_healthy = True
                        print(f"✅ Successfully authorized and connected to Roon Core!")
                        print(f"🎵 Found {len(outputs)} available audio zones")
                        return True
                    else:
                        # Still waiting for full authorization
                        if waited_time == 0:
                            print("📱 Please authorize this extension in Roon:")
                            print("   1. Go to Roon → Settings → Extensions")
                            print("   2. Find 'Tuya Knob Robust Controller'")
                            print("   3. Click 'Enable'")
                            print(f"⏳ Waiting for full authorization... ({max_wait_time - waited_time}s remaining)")
                        elif waited_time % 30 == 0:  # Every 30 seconds
                            print(f"⏳ Still waiting for extension to be enabled in Roon... ({max_wait_time - waited_time}s remaining)")
                        
                        time.sleep(check_interval)
                        waited_time += check_interval
                        continue
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    if "not ready" in error_msg or "not authorized" in error_msg:
                        # Still waiting for authorization
                        if waited_time == 0:
                            print("📱 Please authorize this extension in Roon:")
                            print("   1. Go to Roon → Settings → Extensions")
                            print("   2. Find 'Tuya Knob Robust Controller'")
                            print("   3. Click 'Enable'")
                            print(f"⏳ Waiting... ({max_wait_time - waited_time}s remaining)")
                        elif waited_time % 30 == 0:  # Every 30 seconds
                            print(f"⏳ Still waiting for authorization... ({max_wait_time - waited_time}s remaining)")
                        
                        time.sleep(check_interval)
                        waited_time += check_interval
                        continue
                    else:
                        # Different error - might be connection issue
                        print(f"⚠️  Connection issue: {e}")
                        time.sleep(check_interval)
                        waited_time += check_interval
                        continue
            
            # Timeout reached
            print("⏰ Authorization timeout reached.")
            print("💡 Please check:")
            print("   • Roon Core is running")
            print("   • Network connectivity")
            print("   • Extension is enabled in Roon Settings → Extensions")
            self.roon_connection_healthy = False
            return False
            
        except Exception as e:
            print(f"❌ Failed to setup Roon connection: {e}")
            self.roon_connection_healthy = False
            return False

    def find_zone_output_id(self, zone_id=None, zone_name=None, retries=5, wait_time=2):
        """Find zone output ID by zone ID or name with retry logic"""
        def _find_zone():
            if not self.roonapi:
                return None
            
            outputs = self.roonapi.outputs
            
            # First try exact zone_id match
            if zone_id and zone_id in outputs:
                print(f"✓ Found zone by ID: {zone_id}")
                return zone_id
            
            # Then try zone_name match
            if zone_name:
                for output_id, output_info in outputs.items():
                    display_name = output_info.get('display_name', '')
                    if zone_name == display_name or zone_name in display_name:
                        print(f"✓ Found zone by name: {display_name} (ID: {output_id})")
                        return output_id
            
            # Debug: show available zones
            print("❌ Zone not found. Available zones:")
            for output_id, output_info in outputs.items():
                print(f"   ID: {output_id}, Name: '{output_info.get('display_name', 'Unknown')}'")
            
            return None
        
        # Try multiple times with delays
        for attempt in range(retries):
            print(f"🔍 Looking for zone (attempt {attempt + 1}/{retries})...")
            
            result = self.robust_roon_command(_find_zone)
            if result:
                return result
            
            if attempt < retries - 1:
                print(f"⏳ Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                print("❌ Zone not found after all retries")
                print("💡 You can configure the zone via the web interface")
                return None
        
        return None

    def get_zones(self):
        """Get list of available zones"""
        def _get_zones():
            if not self.roonapi:
                return []
            
            zones = []
            outputs = self.roonapi.outputs
            
            for output_id, output_info in outputs.items():
                zones.append({
                    'id': output_id,
                    'name': output_info.get('display_name', 'Unknown')
                })
            
            return zones
        
        result = self.robust_roon_command(_get_zones)
        return result if result else []

    def get_current_volume(self):
        """Get current volume percentage"""
        def _get_volume():
            if not self.zone_output_id or not self.roonapi:
                return None
            
            try:
                volume = self.roonapi.get_volume_percent(self.zone_output_id)
                return volume
            except Exception as e:
                print(f"⚠️  Error getting volume: {e}")
                return None
        
        return self.robust_roon_command(_get_volume)

    def set_volume(self, volume_percent):
        """Set volume to specific percentage"""
        def _set_volume():
            if not self.zone_output_id or not self.roonapi:
                return False
            
            # Clamp volume to 0-100 range
            volume_percent_clamped = max(0, min(100, volume_percent))
            
            try:
                self.roonapi.set_volume_percent(self.zone_output_id, volume_percent_clamped)
                return True
            except Exception as e:
                print(f"⚠️  Error setting volume: {e}")
                return False
        
        return self.robust_roon_command(_set_volume)

    def change_volume(self, delta):
        """Change volume by delta amount"""
        current = self.get_current_volume()
        if current is not None:
            new_volume = max(0, min(100, current + delta))
            if self.set_volume(new_volume):
                print(f"🔊 Volume: {current}% → {new_volume}%")
                return True
        return False

    def toggle_playback(self):
        """Toggle play/pause state"""
        def _toggle_playback():
            if not self.zone_output_id or not self.roonapi:
                return False
            
            zones = self.roonapi.zones
            current_zone = None
            current_zone_id = None
            
            # Find the zone containing our output
            for zone_id, zone_info in zones.items():
                for output in zone_info.get('outputs', []):
                    if output['output_id'] == self.zone_output_id:
                        current_zone = zone_info
                        current_zone_id = zone_id
                        break
                if current_zone:
                    break
            
            if current_zone and current_zone_id:
                state = current_zone.get('state', 'stopped')
                print(f"🎵 Current playback state: {state}")
                
                try:
                    if state == 'playing':
                        # Use playback_control method with 'pause'
                        self.roonapi.playback_control(current_zone_id, 'pause')
                        print("⏸️  Playback paused")
                    elif state == 'paused':
                        # Use playback_control method with 'play'
                        self.roonapi.playback_control(current_zone_id, 'play')
                        print("▶️  Playback resumed")
                    else:
                        # For stopped state, try 'play'
                        self.roonapi.playback_control(current_zone_id, 'play')
                        print("▶️  Playback started")
                    return True
                except Exception as e:
                    print(f"❌ Playback control failed: {e}")
                    # Try alternative: playpause command
                    try:
                        self.roonapi.playback_control(current_zone_id, 'playpause')
                        print(f"🎵 Playback toggled (via playpause)")
                        return True
                    except Exception as e2:
                        print(f"❌ Alternative playback control failed: {e2}")
                        return False
            else:
                print(f"❌ Could not find zone for output {self.zone_output_id}")
                return False
        
        success = self.robust_roon_command(_toggle_playback)
        if not success:
            print("❌ Failed to toggle playback")

    def setup_mqtt(self):
        """Setup MQTT client with robust connection handling"""
        try:
            # Use the modern callback API (version 2)
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.mqtt_client.on_connect = self.on_mqtt_connect
            self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
            self.mqtt_client.on_message = self.on_mqtt_message
            
            # Add error handling for connection issues
            self.mqtt_client.on_socket_close = self.on_mqtt_socket_close
            self.mqtt_client.on_socket_open = self.on_mqtt_socket_open
            
            print(f"🔌 Connecting to MQTT broker: {self.config['mqtt_broker']}:{self.config['mqtt_port']}")
            self.mqtt_client.connect(self.config['mqtt_broker'], self.config['mqtt_port'], 60)
            
            # Start the MQTT loop in background
            self.mqtt_client.loop_start()
            
            # Wait a bit for connection
            time.sleep(2)
            
            return self.mqtt_connected
            
        except Exception as e:
            print(f"❌ MQTT connection failed: {e}")
            self.mqtt_connected = False
            return False

    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        """Callback for MQTT connection (API v2)"""
        if reason_code == 0:
            print(f"✓ Connected to MQTT and subscribed to {self.config['tuya_knob_topic']}")
            client.subscribe(self.config['tuya_knob_topic'])
            self.mqtt_connected = True
        else:
            print(f"❌ MQTT connection failed with code: {reason_code}")
            self.mqtt_connected = False

    def on_mqtt_disconnect(self, client, userdata, flags, reason_code, properties):
        """Callback for MQTT disconnection (API v2)"""
        print(f"⚠️  Disconnected from MQTT (code: {reason_code})")
        self.mqtt_connected = False
        
        # Auto-reconnect for unexpected disconnections
        if reason_code != 0:
            print("🔄 Attempting to reconnect to MQTT...")
            try:
                time.sleep(2)  # Wait before reconnecting
                client.reconnect()
            except Exception as e:
                print(f"❌ MQTT reconnection failed: {e}")

    def on_mqtt_socket_close(self, client, userdata, socket):
        """Handle socket close events"""
        print("🔌 MQTT socket closed")
        self.mqtt_connected = False

    def on_mqtt_socket_open(self, client, userdata, socket):
        """Handle socket open events"""
        print("🔌 MQTT socket opened")

    def on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT messages from Tuya knob"""
        try:
            payload = json.loads(msg.payload.decode())
            print(f"📡 MQTT message: {payload}")
            
            # Update knob status
            self.knob_last_seen = time.time()
            if 'battery' in payload:
                self.knob_battery = payload['battery']
                print(f"🔋 Battery: {self.knob_battery}%")
            if 'voltage' in payload:
                self.knob_voltage = payload['voltage']
                print(f"⚡ Voltage: {self.knob_voltage}mV")
            if 'linkquality' in payload:
                self.knob_linkquality = payload['linkquality']
                print(f"📶 Signal: {self.knob_linkquality}")
            
            if 'action' in payload:
                action = payload['action']
                self.handle_knob_action(action)
                
        except Exception as e:
            print(f"❌ Error processing MQTT message: {e}")

    def handle_knob_action(self, action):
        """Handle different knob actions"""
        print(f"🎛️  Knob action: {action}")
        
        if not self.zone_output_id:
            print("⚠️  No zone configured - please set zone via web interface")
            return
        
        if action == 'rotate_left':
            self.change_volume(-self.config['volume_step'])
        elif action == 'rotate_right':
            self.change_volume(self.config['volume_step'])
        elif action == 'single':
            self.toggle_playback()
        elif action == 'double':
            if self.set_volume(50):
                print("🔊 Volume set to 50%")
        elif action == 'hold':
            if self.set_volume(0):
                print("🔇 Volume muted (0%)")
        else:
            print(f"❓ Unknown action: {action}")

    def setup_routes(self):
        """Setup Flask routes"""
        @self.app.route('/health')
        def health_check():
            """Health check endpoint"""
            return jsonify({
                'status': 'ok',
                'controller_running': self.controller_running,
                'mqtt_connected': self.mqtt_connected,
                'roon_healthy': self.roon_connection_healthy,
                'knob_battery': self.knob_battery,
                'knob_voltage': self.knob_voltage,
                'knob_last_seen': self.knob_last_seen
            })
        
        @self.app.route('/')
        def index():
            """Main web interface"""
            html_template = '''
<!DOCTYPE html>
<html>
<head>
    <title>Tuya-Roon Controller</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .knob-hero { text-align: center; margin: 20px 0; }
        .knob-image { max-width: 200px; height: auto; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.2); }
        .status { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .status-item { padding: 15px; border-radius: 8px; text-align: center; }
        .status-good { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
        .status-bad { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
        .status-warning { background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }
        .knob-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 15px; margin-bottom: 30px; padding: 20px; background: #f8f9fa; border-radius: 10px; border: 2px solid #e9ecef; }
        .knob-info-title { grid-column: 1 / -1; text-align: center; font-size: 18px; font-weight: bold; color: #495057; margin-bottom: 10px; }
        .knob-status { padding: 12px; border-radius: 6px; text-align: center; font-size: 13px; font-weight: 500; }
        .battery-good { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .battery-medium { background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; }
        .battery-low { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .knob-offline { background: #e9ecef; color: #6c757d; border: 1px solid #ced4da; }
        .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .btn { padding: 12px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; text-decoration: none; display: inline-block; text-align: center; transition: all 0.2s; }
        .btn-primary { background: #007bff; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn:hover { opacity: 0.9; transform: translateY(-1px); }
        .config-section { margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: bold; }
        .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        .knob-instructions { background: #e7f3ff; border: 1px solid #b3d7ff; border-radius: 6px; padding: 15px; margin: 20px 0; }
        .knob-instructions h4 { margin-top: 0; color: #0066cc; }
        .knob-instructions ul { margin-bottom: 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎛️ Tuya-Roon Controller</h1>
            <p>Physical knob volume control for Roon</p>
        </div>
        
        <div class="knob-hero">
            <img src="https://www.zigbee2mqtt.io/images/devices/ERS-10TZBVK-AA.png" 
                 alt="Tuya Smart Knob" 
                 class="knob-image"
                 crossorigin="anonymous">
        </div>
        
        <div class="knob-instructions">
            <h4>📋 Knob Controls</h4>
            <ul>
                <li><strong>Rotate left/right:</strong> Volume down/up (<span id="volume-step-display">±5</span>%)</li>
                <li><strong>Single press:</strong> Play/Pause toggle</li>
                <li><strong>Double press:</strong> Set volume to 50%</li>
                <li><strong>Hold:</strong> Set volume to 0% (mute)</li>
            </ul>
        </div>
        
        <div class="status">
            <div id="controller-status" class="status-item">
                <strong>Controller</strong><br>
                <span id="controller-text">Loading...</span>
            </div>
            <div id="mqtt-status" class="status-item">
                <strong>MQTT</strong><br>
                <span id="mqtt-text">Loading...</span>
            </div>
            <div id="roon-status" class="status-item">
                <strong>Roon</strong><br>
                <span id="roon-text">Loading...</span>
            </div>
        </div>
        
        <div class="knob-info" id="knob-section">
            <div class="knob-info-title">🎛️ Tuya Smart Knob Status</div>
            <div id="knob-battery" class="knob-status knob-offline">
                <strong>🔋 Battery</strong><br>
                <span id="battery-text">Unknown</span>
            </div>
            <div id="knob-voltage" class="knob-status knob-offline">
                <strong>⚡ Voltage</strong><br>
                <span id="voltage-text">Unknown</span>
            </div>
            <div id="knob-last-seen" class="knob-status knob-offline">
                <strong>👁️ Last Seen</strong><br>
                <span id="last-seen-text">Never</span>
            </div>
            <div id="knob-signal" class="knob-status knob-offline">
                <strong>📶 Signal</strong><br>
                <span id="signal-text">Unknown</span>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-success" onclick="startController()">Start Controller</button>
            <button class="btn btn-danger" onclick="stopController()">Stop Controller</button>
            <button class="btn btn-primary" onclick="testVolume()">Test Volume</button>
            <button class="btn btn-primary" onclick="togglePlayback()">Play/Pause</button>
        </div>
        
        <div class="config-section">
            <h3>⚙️ Configuration</h3>
            <div class="form-group">
                <label>Zone:</label>
                <select id="zone-select" onchange="updateZone()">
                    <option value="">Loading zones...</option>
                </select>
            </div>
            <div class="form-group">
                <label>Volume Step:</label>
                <input type="number" id="volume-step" min="1" max="20" onchange="updateConfig()">
            </div>
            <div class="form-group">
                <label>MQTT Broker:</label>
                <input type="text" id="mqtt-broker" onchange="updateConfig()">
            </div>
            <div class="form-group">
                <label>Tuya Knob Topic:</label>
                <input type="text" id="tuya-topic" onchange="updateConfig()">
            </div>
        </div>
    </div>

    <script>
        function updateStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    // Controller status
                    const controllerStatus = document.getElementById('controller-status');
                    const controllerText = document.getElementById('controller-text');
                    if (data.controller_running) {
                        controllerStatus.className = 'status-item status-good';
                        controllerText.textContent = 'Running';
                    } else {
                        controllerStatus.className = 'status-item status-bad';
                        controllerText.textContent = 'Stopped';
                    }
                    
                    // MQTT status
                    const mqttStatus = document.getElementById('mqtt-status');
                    const mqttText = document.getElementById('mqtt-text');
                    if (data.mqtt_connected) {
                        mqttStatus.className = 'status-item status-good';
                        mqttText.textContent = 'Connected';
                    } else {
                        mqttStatus.className = 'status-item status-bad';
                        mqttText.textContent = 'Disconnected';
                    }
                    
                    // Roon status
                    const roonStatus = document.getElementById('roon-status');
                    const roonText = document.getElementById('roon-text');
                    if (data.roon_healthy) {
                        roonStatus.className = 'status-item status-good';
                        roonText.textContent = 'Connected';
                    } else {
                        roonStatus.className = 'status-item status-bad';
                        roonText.textContent = 'Disconnected';
                    }
                    
                    // Knob battery
                    const batteryDiv = document.getElementById('knob-battery');
                    const batteryText = document.getElementById('battery-text');
                    if (data.knob_battery !== null) {
                        batteryText.textContent = data.knob_battery + '%';
                        if (data.knob_battery > 50) {
                            batteryDiv.className = 'knob-status battery-good';
                        } else if (data.knob_battery > 20) {
                            batteryDiv.className = 'knob-status battery-medium';
                        } else {
                            batteryDiv.className = 'knob-status battery-low';
                        }
                    } else {
                        batteryText.textContent = 'Unknown';
                        batteryDiv.className = 'knob-status knob-offline';
                    }
                    
                    // Knob voltage
                    const voltageDiv = document.getElementById('knob-voltage');
                    const voltageText = document.getElementById('voltage-text');
                    if (data.knob_voltage !== null) {
                        voltageText.textContent = (data.knob_voltage / 1000).toFixed(1) + 'V';
                        voltageDiv.className = 'knob-status battery-good';
                    } else {
                        voltageText.textContent = 'Unknown';
                        voltageDiv.className = 'knob-status knob-offline';
                    }
                    
                    // Signal strength (from linkquality if available)
                    const signalDiv = document.getElementById('knob-signal');
                    const signalText = document.getElementById('signal-text');
                    if (data.knob_linkquality !== undefined && data.knob_linkquality !== null) {
                        signalText.textContent = data.knob_linkquality;
                        if (data.knob_linkquality > 150) {
                            signalDiv.className = 'knob-status battery-good';
                        } else if (data.knob_linkquality > 100) {
                            signalDiv.className = 'knob-status battery-medium';
                        } else {
                            signalDiv.className = 'knob-status battery-low';
                        }
                    } else {
                        signalText.textContent = 'Unknown';
                        signalDiv.className = 'knob-status knob-offline';
                    }
                    
                    // Last seen
                    const lastSeenDiv = document.getElementById('knob-last-seen');
                    const lastSeenText = document.getElementById('last-seen-text');
                    if (data.knob_last_seen !== null) {
                        const lastSeen = new Date(data.knob_last_seen * 1000);
                        const now = new Date();
                        const diffSeconds = Math.floor((now - lastSeen) / 1000);
                        
                        if (diffSeconds < 60) {
                            lastSeenText.textContent = diffSeconds + 's ago';
                            lastSeenDiv.className = 'knob-status battery-good';
                        } else if (diffSeconds < 300) {
                            lastSeenText.textContent = Math.floor(diffSeconds / 60) + 'm ago';
                            lastSeenDiv.className = 'knob-status battery-medium';
                        } else {
                            lastSeenText.textContent = Math.floor(diffSeconds / 60) + 'm ago';
                            lastSeenDiv.className = 'knob-status battery-low';
                        }
                    } else {
                        lastSeenText.textContent = 'Never';
                        lastSeenDiv.className = 'knob-status knob-offline';
                    }
                })
                .catch(error => console.error('Error:', error));
        }
        
        function loadConfig() {
            fetch('/api/config')
                .then(response => response.json())
                .then(config => {
                    document.getElementById('volume-step').value = config.volume_step || 5;
                    document.getElementById('mqtt-broker').value = config.mqtt_broker || 'localhost';
                    document.getElementById('tuya-topic').value = config.tuya_knob_topic || 'zigbee2mqtt/TuyaKnob';
                    
                    // Update the volume step display in the instructions
                    document.getElementById('volume-step-display').textContent = '±' + (config.volume_step || 5);
                });
        }
        
        function loadZones() {
            fetch('/api/zones')
                .then(response => response.json())
                .then(zones => {
                    const select = document.getElementById('zone-select');
                    select.innerHTML = '<option value="">Select a zone...</option>';
                    zones.forEach(zone => {
                        const option = document.createElement('option');
                        option.value = zone.id;
                        option.textContent = zone.name;
                        select.appendChild(option);
                    });
                    
                    // Load current config and select current zone
                    fetch('/api/config')
                        .then(response => response.json())
                        .then(config => {
                            if (config.zone_id) {
                                select.value = config.zone_id;
                            }
                        });
                });
        }
        
        function startController() {
            fetch('/api/start', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    updateStatus();
                });
        }
        
        function stopController() {
            fetch('/api/stop', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    updateStatus();
                });
        }
        
        function testVolume() {
            fetch('/api/test-volume', { method: 'POST' })
                .then(response => response.json())
                .then(data => alert(data.message));
        }
        
        function togglePlayback() {
            fetch('/api/toggle-playback', { method: 'POST' })
                .then(response => response.json())
                .then(data => alert(data.message));
        }
        
        function updateZone() {
            const zoneId = document.getElementById('zone-select').value;
            if (zoneId) {
                fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ zone_id: zoneId })
                })
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    if (data.success) updateStatus();
                });
            }
        }
        
        function updateConfig() {
            const config = {
                volume_step: parseInt(document.getElementById('volume-step').value),
                mqtt_broker: document.getElementById('mqtt-broker').value,
                tuya_knob_topic: document.getElementById('tuya-topic').value
            };
            
            fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Update the volume step display immediately
                    document.getElementById('volume-step-display').textContent = '±' + config.volume_step;
                } else {
                    alert('Error updating config: ' + data.message);
                }
            });
        }
        
        // Initialize
        updateStatus();
        loadConfig();
        loadZones();
        
        // Update status every 5 seconds
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>
            '''
            return html_template
        
        @self.app.route('/api/status')
        def api_status():
            """API endpoint for status"""
            current_volume = self.get_current_volume()
            return jsonify({
                'controller_running': self.controller_running,
                'mqtt_connected': self.mqtt_connected,
                'roon_healthy': self.roon_connection_healthy,
                'current_volume': current_volume,
                'zone_name': self.config.get('zone_name'),
                'knob_battery': self.knob_battery,
                'knob_voltage': self.knob_voltage,
                'knob_last_seen': self.knob_last_seen,
                'knob_linkquality': self.knob_linkquality
            })
        
        @self.app.route('/api/zones')
        def api_zones():
            """API endpoint for available zones"""
            zones = self.get_zones()
            return jsonify(zones)
        
        @self.app.route('/api/config', methods=['GET', 'POST'])
        def api_config():
            """API endpoint for configuration"""
            if request.method == 'POST':
                try:
                    data = request.get_json()
                    
                    # Update config
                    for key, value in data.items():
                        if key in self.config:
                            self.config[key] = value
                    
                    # Save config
                    if self.save_config():
                        # If zone changed, restart controller
                        if 'zone_id' in data and self.controller_running:
                            self.stop_controller()
                            time.sleep(1)
                            self.start_controller()
                        
                        return jsonify({
                            'success': True,
                            'message': 'Configuration updated successfully'
                        })
                    else:
                        return jsonify({
                            'success': False,
                            'message': 'Failed to save configuration'
                        })
                        
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'message': f'Error updating configuration: {str(e)}'
                    })
            else:
                return jsonify(self.config)
        
        @self.app.route('/api/start', methods=['POST'])
        def api_start():
            """API endpoint to start controller"""
            if self.controller_running:
                return jsonify({
                    'success': True,
                    'message': 'Controller is already running'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Controller can only be started by restarting the service'
                })
        
        @self.app.route('/api/stop', methods=['POST'])
        def api_stop():
            """API endpoint to stop controller"""
            if self.controller_running:
                return jsonify({
                    'success': True,
                    'message': 'Controller stop requested (restart service to start again)'
                })
            else:
                return jsonify({
                    'success': True,
                    'message': 'Controller is already stopped'
                })
        
        @self.app.route('/api/test-volume', methods=['POST'])
        def api_test_volume():
            """API endpoint to test volume control"""
            current = self.get_current_volume()
            if current is not None:
                test_volume = 25 if current > 50 else 75
                if self.set_volume(test_volume):
                    return jsonify({
                        'success': True,
                        'message': f'Volume test successful: {current}% → {test_volume}%'
                    })
                else:
                    return jsonify({
                        'success': False,
                        'message': 'Volume test failed'
                    })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Could not get current volume'
                })
        
        @self.app.route('/api/toggle-playback', methods=['POST'])
        def api_toggle_playback():
            """API endpoint to toggle playback"""
            self.toggle_playback()
            return jsonify({
                'success': True,
                'message': 'Playback toggle command sent'
            })

    def start_controller(self):
        """Start the controller in a separate thread"""
        if self.controller_running:
            print("⚠️  Controller is already running")
            return True
        
        try:
            self.controller_thread = threading.Thread(target=self.run_controller, daemon=True)
            self.controller_thread.start()
            return True
        except Exception as e:
            print(f"❌ Failed to start controller: {e}")
            return False

    def run_controller(self):
        """Main controller loop"""
        self.controller_running = True
        
        # Setup connections
        if not self.setup_roon():
            print("❌ Cannot start without Roon connection")
            self.controller_running = False
            return
        
        # Load zone from config
        self.zone_output_id = self.find_zone_output_id(
            zone_id=self.config.get('zone_id'),
            zone_name=self.config.get('zone_name')
        )
        
        if not self.zone_output_id:
            print(f"❌ Cannot find zone: {self.config.get('zone_name', 'Unknown')}")
            self.controller_running = False
            return
        
        print(f"✓ Found zone: {self.config.get('zone_name')}")
        
        # Set initial volume
        if self.config.get('volume') is not None:
            print(f"🔊 Setting initial volume to {self.config['volume']}%...")
            self.set_volume(self.config['volume'])
        
        if not self.setup_mqtt():
            print("❌ Cannot start without MQTT connection")
            self.controller_running = False
            return
        
        print("✅ Controller started! Listening for knob events...")
        
        # Keep running until stopped
        while self.controller_running:
            time.sleep(1)

    def stop_controller(self):
        """Stop the controller gracefully"""
        print("🛑 Stopping controller...")
        self.controller_running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.disconnect()
                print("✓ Disconnected from MQTT")
            except:
                pass
        
        self.mqtt_connected = False
        self.roon_connection_healthy = False

    def run_service_mode(self):
        """Run in service mode with web interface always available"""
        print("🚀 Starting Tuya-Roon Controller...")
        
        if not self.setup_roon():
            print("❌ Cannot start without Roon connection")
            return False
        
        # Now that we're properly authorized, try to find the zone
        print("🔍 Searching for configured zone...")
        self.zone_output_id = self.find_zone_output_id(
            zone_id=self.config.get('zone_id'),
            zone_name=self.config.get('zone_name'),
            retries=3,  # Reduced retries since we're now properly authorized
            wait_time=1
        )
        
        # Don't fail if zone not found - allow web interface configuration
        if not self.zone_output_id:
            print("⚠️  Zone not found in current configuration")
            print("🌐 You can select a zone via web interface at http://192.168.1.52:8081")
            print("💡 Available zones will be shown in the web interface dropdown")
        else:
            print(f"✓ Found zone: {self.config.get('zone_name')}")
            
            # Set initial volume only if zone is found
            if self.config.get('volume') is not None:
                print(f"🔊 Setting initial volume to {self.config['volume']}%...")
                self.set_volume(self.config['volume'])
        
        if not self.setup_mqtt():
            print("❌ Cannot start without MQTT connection")
            return False
        
        # Mark controller as running
        self.controller_running = True
        
        print("✅ Service started! Listening for knob events...")
        if self.zone_output_id:
            print("💡 Controls:")
            print("   • Rotate left/right: Volume down/up")
            print("   • Single press: Play/Pause")
            print("   • Double press: Set volume to 50%")
            print("   • Hold: Set volume to 0%")
        else:
            print("⚠️  Zone not configured - please set via web interface")
        
        # Start web interface in a separate thread
        web_thread = threading.Thread(target=self.run_web_server, daemon=True)
        web_thread.start()
        
        try:
            # Instead of loop_forever which can crash, use a more robust approach
            while self.controller_running:
                time.sleep(1)
                
                # Check MQTT connection health
                if not self.mqtt_connected and self.mqtt_client:
                    try:
                        print("🔄 Attempting MQTT reconnection...")
                        self.mqtt_client.reconnect()
                        time.sleep(2)
                    except Exception as e:
                        print(f"❌ MQTT reconnection failed: {e}")
                        time.sleep(5)  # Wait longer before next attempt
                        
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            self.stop_controller()
        
        return True
    
    def run_web_server(self, port=8081):
        """Run the web server in a separate thread"""
        try:
            print(f"🌐 Web interface available at:")
            print(f"   • http://192.168.1.52:{port}")
            print(f"   • http://libraryvolume.local:{port} (if mDNS works)")
            print(f"   • http://localhost:{port} (if accessing from Pi)")
            self.app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
        except Exception as e:
            print(f"⚠️  Web server error: {e}")


def main():
    controller = RobustTuyaRoonController()
    
    # Always run with web interface available
    controller.run_service_mode()


if __name__ == "__main__":
    main()