"""
homeassistant.components.mysensors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the main component that communicates with a MySensors serial gateway.
See http://www.mysensors.org for more information.
"""
import logging
import threading
import yaml
import io
import time
from enum import IntEnum
from collections import namedtuple

from homeassistant import util
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP, EVENT_PLATFORM_DISCOVERED, TEMP_CELCIUS,
    ATTR_SERVICE, ATTR_DISCOVERED)

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

DOMAIN = "mysensors"
DEPENDENCIES = []

DISCOVER_SENSORS = "mysensors.sensors"

SERVICE_SENSOR_FILE_RELOAD = "reload_mysensor_file"

CONF_PORT = "port"
CONF_BAUD = "baudrate"
CONF_TIMEOUT = "timeout"

# mysensors.yaml nodes
CONF_NODES = "nodes"
CONF_NODE_ID = "id"
CONF_SENSOR_ID = "id"
CONF_SENSORS = "sensors"
CONF_ALIAS = "alias"
CONF_SKETCH_NAME = "name"
CONF_SKETCH_VERSION = "version"
CONF_SENSOR_VERSION = "version"
CONF_SENSOR_TYPE = "type"

EVENT_PRESENTATION = "mysensors_presentation"
EVENT_SET = "mysensors_set"
EVENT_REQUEST = "mysensors_request"
EVENT_INTERNAL = "mysensors_internal"

SENSORS_FILE = "mysensors.yaml"

_LOGGER = logging.getLogger(__name__)

MyMessage = namedtuple("MyMessage", [
    'node_id', 'child_id', 'message_type', 'ack', 'sub_type', 'payload'])


def setup(hass, config):
    """ Loads the mysensors configuration. """
    if not HAS_SERIAL:
        _LOGGER.error("Missing pyserial dependency")
        return False

    port = config[DOMAIN].get(CONF_PORT)
    if not port:
        _LOGGER.error("Missing key '%s' for %s", CONF_PORT, DOMAIN)
        return False
    baud = util.convert(config[DOMAIN].get(CONF_BAUD), int, 115200)
    timeout = util.convert(config[DOMAIN].get(CONF_TIMEOUT), float, 1.0)

    gateway = MySerialGateway(hass, port, baud, timeout)
    MySensorController(hass, gateway)

    gateway.start()

    return True


class MySensorController(object):
    """ The controller acts as a broker between the Gateway and the HA system.
        It responds to events where appropriate, and also passes events to the
        Event Bus. """
    def __init__(self, hass, gateway):
        self.hass = hass
        self.gateway = gateway
        hass.bus.listen(EVENT_INTERNAL, self.handle_internal)
        hass.bus.listen(EVENT_PRESENTATION, self.handle_presentation)
        hass.services.register(DOMAIN,
                               SERVICE_SENSOR_FILE_RELOAD,
                               self.read_config)
        self.config = None
        self.read_config()

    def read_config(self, service=None):
        """ Read the sensor configuration file. """
        config_path = self.hass.config.path(SENSORS_FILE)
        try:
            self.config = yaml.load(io.open(config_path, 'r'))
        except FileNotFoundError:
            pass
        if self.config is None:
            self.config = {}
        return self.config

    def save_config(self):
        """ Saves the current config to the configuration file. """
        config_path = self.hass.config.path(SENSORS_FILE)
        yaml.dump(self.config, stream=io.open(config_path, 'w'))

    def next_sensor_id(self):
        """ Returns the first available node id. """
        nodes = self.config.get(CONF_NODES)
        if not nodes:
            return 1
        max_node = max(nodes, key=lambda n: n[CONF_NODE_ID])
        return max_node[CONF_NODE_ID] + 1

    def handle_internal(self, event):
        """ Handles internal protocol messages. """
        msg = MyMessage(**event.data)
        if msg.sub_type == Internal.I_ID_REQUEST:
            self.handle_id_request(msg)
        elif msg.sub_type == Internal.I_CONFIG:
            _LOGGER.info("Node %s requesting config", msg.node_id)
            response = MyMessage(*msg)
            # Just assume celcius means that the user wants metric for now
            # It may make more sense to make this a global config option in
            # the future.
            if self.hass.config.temperature_unit == TEMP_CELCIUS:
                response = response._replace(payload='M')
            else:
                response = response._replace(payload='I')
            self.gateway.write(response)
        elif msg.sub_type == Internal.I_LOG_MESSAGE:
            _LOGGER.info(msg.payload)
        elif msg.sub_type == Internal.I_SKETCH_NAME:
            _LOGGER.info("Sketch name: %s", msg.payload)
            node = self._get_node(msg.node_id)
            node[CONF_SKETCH_NAME] = msg.payload
        elif msg.sub_type == Internal.I_SKETCH_VERSION:
            _LOGGER.info("Sketch version: %s", msg.payload)
            node = self._get_node(msg.node_id)
            node[CONF_SKETCH_VERSION] = msg.payload
        elif msg.sub_type == Internal.I_GATEWAY_READY:
            _LOGGER.info("Gateway ready")
        else:
            _LOGGER.warn("Unimplemented sub-type: %s for %s",
                         msg.sub_type, msg)

    def handle_id_request(self, msg):
        """ Handles a sensor's request for a node id. """
        _LOGGER.info("Requesting an id")
        if not self.config.get(CONF_NODES):
            self.config[CONF_NODES] = []

        next_id = self.next_sensor_id()
        _LOGGER.info("Replying with id %s", next_id)

        response = MyMessage(*msg)
        response = response._replace(ack=0,
                                     sub_type=int(Internal.I_ID_RESPONSE),
                                     payload=next_id)
        self.gateway.write(response)

        self.config[CONF_NODES].append({CONF_NODE_ID: next_id})
        self.save_config()

    def _get_node(self, nodeid):
        """ Returns a node, given a node id. """
        nodes = self.config.get(CONF_NODES)
        for node in nodes:
            if node[CONF_NODE_ID] == nodeid:
                return node
        return None

    def _get_sensor(self, node, sensorid):
        """ Returns a sensor, given a node and sensor id. """
        if not node.get(CONF_SENSORS):
            node[CONF_SENSORS] = []
        for sensor in node[CONF_SENSORS]:
            if sensor[CONF_SENSOR_ID] == sensorid:
                return sensor
        sensor = {CONF_SENSOR_ID: sensorid}
        node[CONF_SENSORS].append(sensor)
        return sensor

    def handle_presentation(self, event):
        """ Handles presentation protocol messages. """
        msg = MyMessage(**event.data)
        node = self._get_node(msg.node_id)
        sensor = self._get_sensor(node, msg.child_id)
        sensor[CONF_SENSOR_VERSION] = msg.payload
        sensor[CONF_SENSOR_TYPE] = msg.sub_type
        self.save_config()

        self.hass.bus.fire(EVENT_PLATFORM_DISCOVERED, {
            ATTR_SERVICE: DISCOVER_SENSORS,
            ATTR_DISCOVERED: node,
        })


class MyMessageType(IntEnum):
    """ MySensors message types """
    # pylint: disable=too-few-public-methods
    presentation = 0        # sent by a node when presenting attached sensors
    set = 1                 # sent from/to sensor when value should be updated
    req = 2                 # requests a variable value
    internal = 3            # internal message
    stream = 4              # OTA firmware updates


class Presentation(IntEnum):
    """ MySensors presentation sub-types """
    # pylint: disable=too-few-public-methods
    S_DOOR = 0                  # Door and window sensors
    S_MOTION = 1                # Motion sensors
    S_SMOKE = 2                 # Smoke sensor
    S_LIGHT = 3                 # Light Actuator (on/off)
    S_DIMMER = 4                # Dimmable device of some kind
    S_COVER = 5                 # Window covers or shades
    S_TEMP = 6                  # Temperature sensor
    S_HUM = 7                   # Humidity sensor
    S_BARO = 8                  # Barometer sensor (Pressure)
    S_WIND = 9                  # Wind sensor
    S_RAIN = 10                 # Rain sensor
    S_UV = 11                   # UV sensor
    S_WEIGHT = 12               # Weight sensor for scales etc.
    S_POWER = 13                # Power measuring device, like power meters
    S_HEATER = 14               # Heater device
    S_DISTANCE = 15             # Distance sensor
    S_LIGHT_LEVEL = 16          # Light sensor
    S_ARDUINO_NODE = 17         # Arduino node device
    S_ARDUINO_RELAY = 18        # Arduino repeating node device
    S_LOCK = 19                 # Lock device
    S_IR = 20                   # Ir sender/receiver device
    S_WATER = 21                # Water meter
    S_AIR_QUALITY = 22          # Air quality sensor e.g. MQ-2
    S_CUSTOM = 23               # Use this for custom sensors
    S_DUST = 24                 # Dust level sensor
    S_SCENE_CONTROLLER = 25     # Scene controller device


class SetReq(IntEnum):
    """ MySensors set/req sub-types """
    # pylint: disable=too-few-public-methods
    V_TEMP = 0              # Temperature
    V_HUM = 1               # Humidity
    V_LIGHT = 2             # Light status. 0=off 1=on
    V_DIMMER = 3            # Dimmer value. 0-100%
    V_PRESSURE = 4          # Atmospheric Pressure
    # Weather forecast. One of "stable", "sunny", "cloudy", "unstable",
    # "thunderstorm" or "unknown"
    V_FORECAST = 5
    V_RAIN = 6              # Amount of rain
    V_RAINRATE = 7          # Rate of rain
    V_WIND = 8              # Windspeed
    V_GUST = 9              # Gust
    V_DIRECTION = 10        # Wind direction
    V_UV = 11               # UV light level
    V_WEIGHT = 12           # Weight (for scales etc)
    V_DISTANCE = 13         # Distance
    V_IMPEDANCE = 14        # Impedance value
    # Armed status of a security sensor.  1=Armed, 0=Bypassed
    V_ARMED = 15
    # Tripped status of a security sensor. 1=Tripped, 0=Untripped
    V_TRIPPED = 16
    V_WATT = 17             # Watt value for power meters
    V_KWH = 18              # Accumulated number of KWH for a power meter
    V_SCENE_ON = 19         # Turn on a scene
    V_SCENE_OFF = 20        # Turn of a scene
    # Mode of header. One of "Off", "HeatOn", "CoolOn", or "AutoChangeOver"
    V_HEATER = 21
    V_HEATER_SW = 22        # Heater switch power. 1=On, 0=Off
    V_LIGHT_LEVEL = 23      # Light level. 0-100%
    V_VAR1 = 24             # Custom value
    V_VAR2 = 25             # Custom value
    V_VAR3 = 26             # Custom value
    V_VAR4 = 27             # Custom value
    V_VAR5 = 28             # Custom value
    V_UP = 29               # Window covering. Up.
    V_DOWN = 30             # Window covering. Down.
    V_STOP = 31             # Window covering. Stop.
    V_IR_SEND = 32          # Send out an IR-command
    V_IR_RECEIVE = 33       # This message contains a received IR-command
    V_FLOW = 34             # Flow of water (in meter)
    V_VOLUME = 35           # Water volume
    V_LOCK_STATUS = 36      # Set or get lock status. 1=Locked, 0=Unlocked
    V_DUST_LEVEL = 37       # Dust level
    V_VOLTAGE = 38          # Voltage level
    V_CURRENT = 39          # Current level


class Internal(IntEnum):
    """ MySensors internal sub-types """
    # pylint: disable=too-few-public-methods
    # Use this to report the battery level (in percent 0-100).
    I_BATTERY_LEVEL = 0
    # Sensors can request the current time from the Controller using this
    # message. The time will be reported as the seconds since 1970
    I_TIME = 1
    # Sensors report their library version at startup using this message type
    I_VERSION = 2
    # Use this to request a unique node id from the controller.
    I_ID_REQUEST = 3
    # Id response back to sensor. Payload contains sensor id.
    I_ID_RESPONSE = 4
    # Start/stop inclusion mode of the Controller (1=start, 0=stop).
    I_INCLUSION_MODE = 5
    # Config request from node. Reply with (M)etric or (I)mperal back to sensor
    I_CONFIG = 6
    # When a sensor starts up, it broadcast a search request to all neighbor
    # nodes. They reply with a I_FIND_PARENT_RESPONSE.
    I_FIND_PARENT = 7
    # Reply message type to I_FIND_PARENT request.
    I_FIND_PARENT_RESPONSE = 8
    # Sent by the gateway to the Controller to trace-log a message
    I_LOG_MESSAGE = 9
    # A message that can be used to transfer child sensors
    # (from EEPROM routing table) of a repeating node.
    I_CHILDREN = 10
    # Optional sketch name that can be used to identify sensor in the
    # Controller GUI
    I_SKETCH_NAME = 11
    # Optional sketch version that can be reported to keep track of the version
    # of sensor in the Controller GUI.
    I_SKETCH_VERSION = 12
    # Used by OTA firmware updates. Request for node to reboot.
    I_REBOOT = 13
    # Send by gateway to controller when startup is complete
    I_GATEWAY_READY = 14


class MySerialGateway(threading.Thread):
    """ Communicates via a serial port with a MySensors Gateway. """
    def __init__(self, hass, port, baud, timeout):
        super(MySerialGateway, self).__init__()
        self.hass = hass
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._stop_event = threading.Event()
        self.conn = None

    def close(self):
        """ Closes the serial connection. """
        if self.conn:
            _LOGGER.info("Closing %s", self.port)
            self.conn.close()
            self.conn = None

    def connect(self):
        """ Connects to the configured serial port. """
        if self.conn:
            return True
        _LOGGER.info("Connecting to %s @ %s, timeout %s", self.port,
                     self.baud, self.timeout)
        try:
            self.conn = serial.Serial(self.port, baudrate=self.baud,
                                      timeout=self.timeout)
        except serial.SerialException as ex:
            _LOGGER.warn(ex)
            return False
        return True

    def run(self):
        """ The main thread that connects to and reads messages from the
            gateway via the serial port. """
        self.hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP,
                                  lambda event: self._stop_event.set())
        while not self._stop_event.set():
            if not self.conn and not self.connect():
                time.sleep(10)
                continue
            try:
                line = self.conn.readline()
            except serial.SerialException as ex:
                _LOGGER.warn(ex)
                continue
            except TypeError as ex:
                _LOGGER.warn(ex)
                self.close()
                self.connect()
                continue
            if line:
                self._handle_line(line.decode())
        self.close()

    def write(self, msg):
        """ Send a MyMessage namedtuple to the gateway. """
        out = ';'.join([str(f) for f in msg]) + '\n'
        _LOGGER.info("writing %s", out)
        self.conn.write(out.encode())

    def _handle_line(self, line):
        """ Parses a message from a line and dispatches it. """
        fields = line.split(';')
        fields = [int(f) for f in fields[:-1]] + [fields[-1].rstrip()]
        msg = MyMessage(*fields)
        try:
            mtype = MyMessageType(msg.message_type)
        except ValueError:
            _LOGGER.error("Unknown message type: %s", msg.message_type)
            return
        if mtype == MyMessageType.presentation:
            self.hass.bus.fire(EVENT_PRESENTATION, msg._asdict())
        elif mtype == MyMessageType.set:
            self.hass.bus.fire(EVENT_SET, msg._asdict())
        elif mtype == MyMessageType.req:
            self.hass.bus.fire(EVENT_REQUEST, msg._asdict())
        elif mtype == MyMessageType.internal:
            self.hass.bus.fire(EVENT_INTERNAL, msg._asdict())
        else:
            _LOGGER.warn("Unsupported message type: %s", mtype)
