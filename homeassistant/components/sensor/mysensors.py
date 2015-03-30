"""
homeassistant.components.sensor.mysensors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

MySensors sensors.
"""
import logging

from homeassistant.helpers.entity import Entity
from homeassistant.components.mysensors import (
    CONF_NODE_ID, CONF_ALIAS)

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
        # TODO: include sensor data in state attributes
        return {}
