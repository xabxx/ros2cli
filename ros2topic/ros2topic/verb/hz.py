# Software License Agreement (BSD License)
#
# Copyright (c) 2008, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# This file is originally from:
# https://github.com/ros/ros_comm/blob/6e5016f4b2266d8a60c9a1e163c4928b8fc7115e/tools/rostopic/src/rostopic/__init__.py

import functools
import importlib
import math

import time

import rclpy

from rclpy.expand_topic_name import expand_topic_name
from rclpy.qos import qos_profile_sensor_data
from rclpy.validate_full_topic_name import validate_full_topic_name
from ros2cli.node.direct import DirectNode
from ros2topic.api import get_topic_names_and_types
from ros2topic.api import TopicNameCompleter
from ros2topic.verb import VerbExtension


class HzVerb(VerbExtension):
    """Print the average publishing rate to screen."""

    def add_arguments(self, parser, cli_name):
        arg = parser.add_argument(
            'topic_name',
            help="Name of the ROS topic to listen to (e.g. '/chatter')")
        arg.completer = TopicNameCompleter(
            include_hidden_topics_key='include_hidden_topics')
        # TODO to add --filter
        # TODO to add --wall-time
        parser.add_argument(
            '--window', '-w', default=-1,
            help='window size, in # of messages, for calculating rate, '
                 'string to (default: -1)')

    def main(self, *, args):
        return main(args)


def main(args):
    try:
        window_size = int(args.window)
    except ValueError as e:
        raise RuntimeError('Value must be non-negative integer')

    with DirectNode(args) as node:
        _ros2topic_hz(node.node, args.topic_name, window_size=window_size)


class ROSTopicHz(object):
    """ROSTopicHz receives messages for a topic and computes frequency."""

    def __init__(self, window_size):
        import threading
        from collections import defaultdict
        self.lock = threading.Lock()
        self.last_printed_tn = 0
        self.msg_t0 = -1
        self.msg_tn = 0
        self.times = []
        self._last_printed_tn = defaultdict(int)
        self._msg_t0 = defaultdict(lambda: -1)
        self._msg_tn = defaultdict(int)
        self._times = defaultdict(list)

        # can't have infinite window size due to memory restrictions
        if window_size < 0:
            window_size = 50000
        self.window_size = window_size

    def get_last_printed_tn(self, topic=None):
        if topic is None:
            return self.last_printed_tn
        return self._last_printed_tn[topic]

    def set_last_printed_tn(self, value, topic=None):
        if topic is None:
            self.last_printed_tn = value
        self._last_printed_tn[topic] = value

    def get_msg_t0(self, topic=None):
        if topic is None:
            return self.msg_t0
        return self._msg_t0[topic]

    def set_msg_t0(self, value, topic=None):
        if topic is None:
            self.msg_t0 = value
        self._msg_t0[topic] = value

    def get_msg_tn(self, topic=None):
        if topic is None:
            return self.msg_tn
        return self._msg_tn[topic]

    def set_msg_tn(self, value, topic=None):
        if topic is None:
            self.msg_tn = value
        self._msg_tn[topic] = value

    def get_times(self, topic=None):
        if topic is None:
            return self.times
        return self._times[topic]

    def set_times(self, value, topic=None):
        if topic is None:
            self.times = value
        self._times[topic] = value

    def callback_hz(self, m, topic=None):
        """
        Calculate interval time.

        :param m: Message instance
        :param topic: Topic name
        """
        with self.lock:
            # TODO uses ROSTime as the default time source and Walltime only if requested
            curr = time.time()
            if self.get_msg_t0(topic=topic) < 0 or self.get_msg_t0(topic=topic) > curr:
                self.set_msg_t0(curr, topic=topic)
                self.set_msg_tn(curr, topic=topic)
                self.set_times([], topic=topic)
            else:
                self.get_times(topic=topic).append(curr - self.get_msg_tn(topic=topic))
                self.set_msg_tn(curr, topic=topic)

            # only keep statistics for the last 10000 messages so as not to run out of memory
            if len(self.get_times(topic=topic)) > 10000:
                self.get_times(topic=topic).pop(0)

    def get_hz(self, topic=None):
        """
        Calculate the average publising rate.

        :param topic: topic name, ``list`` of ``str``
        :returns: tuple of stat results
            (rate, min_delta, max_delta, standard deviation, window number)
            None when waiting for the first message or there is no new one
        """
        if not self.get_times(topic=topic):
            return
        elif self.get_msg_tn(topic=topic) < self.get_last_printed_tn(topic=topic) + 1:
            return
        with self.lock:
            # frequency
            n = len(self.get_times(topic=topic))
            mean = sum(self.get_times(topic=topic)) / n
            rate = 1. / mean if mean > 0. else 0

            # std dev
            std_dev = math.sqrt(sum((x - mean)**2 for x in self.get_times(topic=topic)) / n)

            # min and max
            max_delta = max(self.get_times(topic=topic))
            min_delta = min(self.get_times(topic=topic))

            self.set_last_printed_tn(self.get_msg_tn(topic=topic), topic=topic)

        return rate, min_delta, max_delta, std_dev, n + 1

    def print_hz(self, topic=None):
        """Print the average publishing rate to screen."""
        ret = self.get_hz(topic)
        if ret is None:
            return
        rate, min_delta, max_delta, std_dev, window = ret
        print('average rate: %.3f\n\tmin: %.3fs max: %.3fs std dev: %.5fs window: %s'
              % (rate, min_delta, max_delta, std_dev, window))
        return


def get_msg_module(node, topic):
    """
    Get message module based on topic name.

    :param topic: topic name, ``list`` of ``str``
    """
    topic_names_and_types = get_topic_names_and_types(node=node)
    try:
        expanded_name = expand_topic_name(topic, node.get_name(), node.get_namespace())
    except ValueError as e:
        raise RuntimeError(e)
    try:
        validate_full_topic_name(expanded_name)
    except rclpy.exceptions.InvalidTopicNameException as e:
        raise RuntimeError(e)
    for n, t in topic_names_and_types:
        if n == expanded_name:
            if len(t) > 1:
                raise RuntimeError(
                    "Cannot echo topic '%s', as it contains more than one type: [%s]" %
                    (topic, ', '.join(t))
                )
            message_type = t[0]
            break
    else:
        raise RuntimeError(
            'Could not determine the type for the passed topic')

    # TODO(dirk-thomas) this logic should come from a rosidl related package
    try:
        package_name, message_name = message_type.split('/', 2)
        if not package_name or not message_name:
            raise ValueError()
    except ValueError:
        raise RuntimeError('The passed message type is invalid')
    module = importlib.import_module(package_name + '.msg')
    return getattr(module, message_name)


def _ros2topic_hz(node, topic, window_size=-1):
    """
    Periodically print the publishing rate of a topic to console until shutdown.

    :param topic: topic name, ``list`` of ``str``
    :param window_size: number of messages to average over, -1 for infinite, ``int``
    """
    msg_module = get_msg_module(node, topic)
    rt = ROSTopicHz(window_size)
    node.create_subscription(
        msg_module,
        topic,
        functools.partial(rt.callback_hz, topic=topic),
        qos_profile=qos_profile_sensor_data)

    while rclpy.ok():
        rclpy.spin_once(node)
        rt.print_hz(topic)
