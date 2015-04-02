"""
homeassistant.components.sensor.mysensors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

MySensors sensors.
"""
import logging

from homeassistant.helpers.entity import Entity
from homeassistant.components.mysensors import (
    MyMessage, SetReq, EVENT_SET, CONF_NODE_ID, CONF_ALIAS)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """ Sets up the MySensors platform. """
    if discovery_info is None:
        return
    add_devices([MySensorNode(discovery_info)])


class MySensorNode(Entity):
    """ Represents a MySensors node. """

    def __init__(self, node):
        self.node = node
        self.attributes = {}

    @property
    def should_poll(self):
        """ Updates are pushed from the gateway. """
        return False

    @property
    def unique_id(self):
        """ Returns the id of this sensor """
        return "{}.{}".format(self.__class__, self.node[CONF_NODE_ID])

    @property
    def name(self):
        """ Returns the name of the sensor if any. """
        alias = self.node.get(CONF_ALIAS)
        if alias:
            return alias
        return 'MySensor {}'.format(self.node[CONF_NODE_ID])

    @property
    def state_attributes(self):
        """ Returns the state attributes. """
        return self.attributes.copy()

    def _handle_set(self, event):
        """ Updates the current state of the node based on the event. """
        msg = MyMessage(**event.data)
        if msg.node_id != self.node[CONF_NODE_ID]:
            return
        var = SetReq(msg.sub_type)
        self.attributes[var.name] = msg.payload
        self.update_ha_state()

    def setup(self):
        """ Setup an event listener so that we can update the device state. """
        self.hass.bus.listen(EVENT_SET, self._handle_set)
