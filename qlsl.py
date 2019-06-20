"""
    Stream 3D and 6DOF data from QTM and pass it through an LSL outlet.
"""

import asyncio
from enum import Enum
import logging
import time
import xml.etree.ElementTree as ET

from pylsl import cf_float32, StreamInfo, StreamOutlet
import qtm
from qtm import QRTEvent
from qtm.packet import QRTComponentType

LOG = logging.getLogger("qlsl")
QTM_DEFAULT_PORT = 22223

# TODO: tests
def xml_parse_parameters(xml_string):
    print(xml_string)
    xml = ET.fromstring(xml_string)
    xml_general = xml.find("./General")
    xml_3d = xml.find("./The_3D")
    cameras = []
    markers = []
    frequency = None
    if xml_general:
        xml_cameras = xml_general.findall("./Camera")
        for xml_camera in xml_cameras:
            camera = {}
            for xml_camera_param in xml_camera:
                tag = xml_camera_param.tag.lower()
                if tag in ["id", "model", "serial", "mode", "video_frequency"]:
                    camera[tag] = xml_camera_param.text
            cameras.append(camera)
        frequency = xml_general.findtext("./Frequency")
    if xml_3d:
        xml_label_names = xml_3d.findall("./Label/Name")
        for xml_name in xml_label_names:
            markers.append(xml_name.text)
    return {
        "frequency": frequency,
        "cameras": cameras,
        "markers": markers,
    }

# TODO: tests
def lsl_new_stream_info(qtm_host, qtm_port, qtm_params):
    channel_count = 3*len(qtm_params["markers"])
    info = StreamInfo(name="Qualisys",
        type="Mocap",
        channel_count=channel_count,
        channel_format=cf_float32,
        source_id="{}:{}".format(qtm_host, qtm_port),
    )
    channels = info.desc().append_child("channels")
    def append_channel(marker, axis):
        channels.append_child("channel") \
            .append_child_value("label", "{}_{}".format(marker, axis)) \
            .append_child_value("marker", marker) \
            .append_child_value("type", "Position" + axis) \
            .append_child_value("unit", "millimeters")
    for marker in qtm_params["markers"]:
        append_channel(marker, "X")
        append_channel(marker, "Y")
        append_channel(marker, "Z")
    info.desc().append_child("acquisition") \
        .append_child_value("model", "Qualisys")
    return info

# TODO: tests
def qtm_packet_to_lsl_sample(params, packet):
    if QRTComponentType.Component3d not in packet.components:
        return
    marker_count = len(params["markers"])
    header, markers = packet.get_3d_markers()
    if header.marker_count != marker_count:
        # TODO
        raise RuntimeError("Expected {} but got {} markers from QTM packet".format(marker_count, header.marker_count))
    sample = []
    for marker in markers:
        sample.append(marker.x)
        sample.append(marker.y)
        sample.append(marker.z)
    return sample

class State(Enum):
    INITIAL = 1
    WAITING = 2
    STREAMING = 3
    STOPPED = 4

class Link:
    def __init__(self, host, port, on_state_changed, on_error):
        self.host = host
        self.port = port
        self._on_state_changed = on_state_changed
        self._on_error = on_error

        self.conn = None
        self.params = {}
        self.receiver_queue = None
        self.receiver_task = None
        self.lsl_info = None
        self.lsl_outlet = None
        self.packet_count = 0
    
    def set_state(self, state):
        self.state = state
        self.on_state_changed(self.state)
    
    def on_state_changed(self, new_state):
        if self._on_state_changed:
            self._on_state_changed(new_state)
    
    def on_error(self, msg):
        if self._on_error:
            self._on_error(msg)
    
    def on_event(self, event):
        # TODO: Test live capture stream
        if self.state == State.WAITING:
            if event in [QRTEvent.EventRTfromFileStarted]:
                asyncio.ensure_future(self.start_stream())
        elif self.state == State.STREAMING:
            if event in [QRTEvent.EventRTfromFileStopped]:
                asyncio.ensure_future(self.stop_stream())
        
    def on_disconnect(self, exc):
        self.set_state(State.STOPPED)
        if exc:
            LOG.debug("on_disconnect: {}".format(exc))
            self.on_error(exc)
    
    def lsl_open_stream_outlet(self):
        self.lsl_info = lsl_new_stream_info(self.host, self.port, self.params)
        self.lsl_outlet = StreamOutlet(self.lsl_info, 32, 360)
    
    async def poll_server_state(self):
        await self.conn.get_state()
    
    async def start_stream(self):
        packet = await self.conn.get_parameters(parameters=["general", "3d", "6d"])
        params = xml_parse_parameters(packet.decode("utf-8"))
        if len(params["markers"]) == 0:
            LOG.debug("zero markers")
            return
        self.params = params
        self.receiver_queue = asyncio.Queue()
        self.receiver_task = asyncio.ensure_future(self.stream_receiver())
        self.lsl_open_stream_outlet()
        await self.conn.stream_frames(components=["3d"], on_packet=self.receiver_queue.put_nowait)
        self.set_state(State.STREAMING)

    async def stop_stream(self):
        self.receiver_queue.put_nowait(None)
        await self.receiver_task
        await self.conn.stream_frames_stop()
        self.params = {}
        self.receiver_queue = None
        self.receiver_task = None
        self.lsl_info = None
        self.lsl_outlet = None
        self.set_state(State.WAITING)

    async def stream_receiver(self):
        LOG.debug("qtm_receiver enter")
        try:
            while True:
                packet = await self.receiver_queue.get()
                if packet is None:
                    break
                sample = qtm_packet_to_lsl_sample(self.params, packet)
                if len(sample) > 0:
                    self.packet_count += 1
                    self.lsl_outlet.push_sample(sample)
        finally:
            LOG.debug("qtm_receiver exit")

class LinkError(Exception):
    pass

async def setup_link(
    qtm_host,
    qtm_port=QTM_DEFAULT_PORT,
    on_state_changed=None,
    on_error=None
):
    LOG.debug("link enter")
    link = Link(qtm_host, qtm_port, on_state_changed, on_error)
    link.conn = await qtm.connect(
        host=qtm_host, port=qtm_port,
        on_event=link.on_event, on_disconnect=link.on_disconnect,
    )
    if link.conn is None:
        raise LinkError("Failed to connect to QTM on '{}:{}'".format(qtm_host, qtm_port))
    link.set_state(State.WAITING)
    LOG.debug("link exit")
    return link

async def teardown_link(link):
    LOG.debug("unlink enter")
    if link.state == State.STREAMING:
        await link.stop_stream()
    link.conn.disconnect()
    LOG.debug("unlink exit")
