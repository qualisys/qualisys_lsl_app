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
QTM_DEFAULT_VERSION = "1.19"

def xml_parse_parameters_general(xml_general):
    frequency = None
    cameras = []
    xml_cameras = xml_general.findall("./Camera")
    for xml_camera in xml_cameras:
        camera = {}
        for xml_camera_param in xml_camera:
            tag = xml_camera_param.tag.lower()
            if tag in ["id", "model", "serial", "mode", "video_frequency"]:
                camera[tag] = xml_camera_param.text
        cameras.append(camera)
    frequency = xml_general.findtext("./Frequency")
    return {
        "frequency": frequency,
        "cameras": cameras,
    }

def xml_parse_parameters_3d(xml_3d):
    xml_label_names = xml_3d.findall("./Label/Name")
    markers = []
    for xml_name in xml_label_names:
        markers.append(xml_name.text)
    return {
        "markers": markers,
    }

def xml_parse_parameters_6d(xml_6d):
    bodies = []
    xml_bodies = xml_6d.findall("./Body")
    for xml_body in xml_bodies:
        body = {
            "points": []
        }
        for xml_body_param in xml_body:
            tag = xml_body_param.tag.lower()
            if tag in ["name"]:
                body[tag] = xml_body_param.text
            elif tag == "point":
                point = {}
                for xml_point_param in xml_body_param:
                    tag = xml_point_param.tag.lower()
                    if tag in ["x", "y", "z"]:
                        point[tag] = xml_point_param.text
                body["points"].append(point)
        bodies.append(body)
    xml_euler = xml_6d.find("./Euler")
    euler = {}
    for xml_euler_param in xml_euler:
        tag = xml_euler_param.tag.lower()
        if tag in ["first", "second", "third"]:
            euler[tag] = xml_euler_param.text
    return {
        "bodies": bodies,
        "euler": euler,
    }

def xml_parse_parameters(xml_string):
    print(xml_string)
    xml = ET.fromstring(xml_string)
    params = QTMParameters()
    xml_general = xml.find("./General")
    if xml_general:
        params.general = xml_parse_parameters_general(xml_general)
    xml_3d = xml.find("./The_3D")
    if xml_3d:
        params.the_3d = xml_parse_parameters_3d(xml_3d)
    xml_6d = xml.find("./The_6D")
    if xml_6d:
        params.the_6d = xml_parse_parameters_6d(xml_6d)
    return params

class QTMParameters:
    def __init__(self):
        self.general = {}
        self.the_3d = {}
        self.the_6d = {}
    
    def markers(self):
        try:
            return self.the_3d["markers"]
        except KeyError:
            return []
    
    def marker_count(self):
        return len(self.markers())
    
    def bodies(self):
        try:
            return self.the_6d["bodies"]
        except KeyError:
            return []
    
    def body_count(self):
        return len(self.bodies())

class State(Enum):
    INITIAL = 1
    WAITING = 2
    STREAMING = 3
    STOPPING = 4
    STOPPED = 5

class Link:
    def __init__(self, host, port, on_state_changed, on_error):
        self.host = host
        self.port = port
        self._on_state_changed = on_state_changed
        self._on_error = on_error

        self.state = State.INITIAL
        self.conn = None
        self.packet_count = 0
        self.reset_stream_context()
    
    def reset_stream_context(self):
        self.params = QTMParameters()
        self.receiver_queue = None
        self.receiver_task = None
        self.lsl_info = None
        self.lsl_outlet = None
    
    def set_state(self, state):
        prev_state = self.state
        self.state = state
        self.on_state_changed(self.state)
        return prev_state
    
    def is_stopped(self):
        return self.state in [State.INITIAL, State.STOPPED]
    
    def on_state_changed(self, new_state):
        if self._on_state_changed:
            self._on_state_changed(new_state)
    
    def on_error(self, msg):
        if self._on_error:
            self._on_error(msg)
    
    def on_event(self, event):
        # TODO: Check for events from live capture stream
        if self.state == State.WAITING:
            if event in [QRTEvent.EventRTfromFileStarted]:
                asyncio.ensure_future(self.start_stream())
        elif self.state == State.STREAMING:
            if event in [QRTEvent.EventRTfromFileStopped]:
                asyncio.ensure_future(self.stop_stream())
        
    def on_disconnect(self, exc):
        if self.is_stopped(): return
        if self.state != State.STOPPING:
            self.err_disconnect("Disconnected from QTM")
        if exc:
            LOG.debug("on_disconnect: {}".format(exc))

    def lsl_channel_count(self):
        return 3*self.params.marker_count() + 6*self.params.body_count()

    def lsl_stream_info_add_markers(self, channels, markers):
        def append_channel(marker, component):
            label = "{}_{}".format(marker, component)
            channels.append_child("channel") \
                .append_child_value("label", label) \
                .append_child_value("marker", marker) \
                .append_child_value("type", "Position" + component) \
                .append_child_value("unit", "millimeters")
        for marker in self.params.markers():
            markers.append_child("marker") \
                .append_child_value("label", marker)
            append_channel(marker, "X")
            append_channel(marker, "Y")
            append_channel(marker, "Z")

    def lsl_stream_info_add_6dof(self, channels, objects):
        def append_channel(body, base_type, component, unit):
            label = "{}_{}".format(body, component)
            channels.append_child("channel") \
                .append_child_value("label", label) \
                .append_child_value("object", body) \
                .append_child_value("type", base_type + component) \
                .append_child_value("unit", unit)
        euler_angle_to_component = {
            "pitch": "P",
            "roll": "R",
            "yaw": "H",
        }
        def append_position_channel(body, component):
            append_channel(body, "Position", component, "millimeters")
        def append_orientation_channel(body, angle):
            component = euler_angle_to_component[angle.lower()]
            append_channel(body, "Orientation", component, "degrees")
        for body in self.params.bodies():
            name = body["name"]
            objects.append_child("object") \
                .append_child_value("class", "Rigid") \
                .append_child_value("label", name)
            append_position_channel(name, "X")
            append_position_channel(name, "Y")
            append_position_channel(name, "Z")
            angles = self.params.the_6d["euler"]
            append_orientation_channel(name, angles["first"])
            append_orientation_channel(name, angles["second"])
            append_orientation_channel(name, angles["third"])

    def lsl_new_stream_info(self):
        info = StreamInfo(
            name="Qualisys",
            type="Mocap",
            channel_count=self.lsl_channel_count(),
            channel_format=cf_float32,
            source_id="{}:{}".format(self.host, self.port),
        )
        channels = info.desc().append_child("channels")
        setup = info.desc().append_child("setup")
        markers = setup.append_child("markers")
        objects = setup.append_child("objects")
        # Note: order of function calls is important as they append to channels
        self.lsl_stream_info_add_markers(channels, markers)
        self.lsl_stream_info_add_6dof(channels, objects)
        info.desc().append_child("acquisition") \
            .append_child_value("model", "Qualisys")
        return info
    
    def lsl_open_stream_outlet(self):
        self.lsl_info = self.lsl_new_stream_info()
        self.lsl_outlet = StreamOutlet(self.lsl_info, 32, 360)

    def qtm_packet_to_lsl_sample(self, packet):
        sample = []
        if QRTComponentType.Component3d in packet.components:
            _, markers = packet.get_3d_markers()
            for marker in markers:
                sample.append(marker.x)
                sample.append(marker.y)
                sample.append(marker.z)
        if QRTComponentType.Component6dEuler in packet.components:
            _, bodies = packet.get_6d_euler()
            for position, rotation in bodies:
                sample.append(position.x)
                sample.append(position.y)
                sample.append(position.z)
                sample.append(rotation.a1)
                sample.append(rotation.a2)
                sample.append(rotation.a3)
        if len(sample) != self.lsl_channel_count():
            self.err_disconnect("Stream aborted: QTM stream data inconsistent with LSL metadata")
            return []
        return sample
    
    def err_disconnect(self, err_msg):
        asyncio.ensure_future(self.shutdown(err_msg))

    async def shutdown(self, err_msg=None):
        try:
            LOG.debug("shutdown enter")
            if not self.is_stopped():
                prev_state = self.set_state(State.STOPPING)
                if prev_state == State.STREAMING:
                    await self.stop_stream()
                if self.conn and self.conn.has_transport():
                    self.conn.disconnect()
                self.conn = None
        finally:
            self.set_state(State.STOPPED)
            if err_msg:
                self.on_error(err_msg)
            LOG.debug("shutdown exit")

    async def poll_qtm_state(self):
        try:
            await self.conn.get_state()
        except qtm.QRTCommandException as ex:
            LOG.debug("get_state exception: " + ex)
            self.err_disconnect("QTM exception: " + ex)

    async def stop_stream(self):
        if self.conn and self.conn.has_transport():
            try:
                await self.conn.stream_frames_stop()
            except qtm.QRTCommandException as ex:
                LOG.debug("stream_frames_stop exception: " + ex)
        if self.receiver_queue:
            self.receiver_queue.put_nowait(None)
            await self.receiver_task
        self.reset_stream_context()
        if self.state == State.STREAMING:
            self.set_state(State.WAITING)
    
    async def start_stream(self):
        try:
            packet = await self.conn.get_parameters(
                parameters=["general", "3d", "6d"],
            )
        except qtm.QRTCommandException as ex:
            LOG.debug("get_parameters exception: " + ex)
            self.err_disconnect("QTM exception: " + ex)
            return
        params = xml_parse_parameters(packet.decode("utf-8"))
        if params.marker_count() == 0 or params.body_count() == 0:
            self.err_disconnect("QTM is streaming but not any 3D or 6DOF data")
            LOG.debug("marker_count {} body_count {}".format(
                params.marker_count(), params.body_count()
            ))
            return
        self.params = params
        self.receiver_queue = asyncio.Queue()
        self.receiver_task = asyncio.ensure_future(self.stream_receiver())
        self.lsl_open_stream_outlet()
        try:
            await self.conn.stream_frames(
                components=["3d", "6deuler"],
                on_packet=self.receiver_queue.put_nowait,
            )
            self.set_state(State.STREAMING)
        except qtm.QRTCommandException as ex:
            LOG.debug("stream_frames exception: " + ex)
            self.err_disconnect("QTM exception: " + ex)

    async def stream_receiver(self):
        try:
            LOG.debug("qtm_receiver enter")
            while True:
                packet = await self.receiver_queue.get()
                if packet is None:
                    break
                sample = self.qtm_packet_to_lsl_sample(packet)
                if len(sample) > 0:
                    self.packet_count += 1
                    self.lsl_outlet.push_sample(sample)
        finally:
            LOG.debug("qtm_receiver exit")

class LinkError(Exception):
    pass

async def init_link(
    qtm_host,
    qtm_port=QTM_DEFAULT_PORT,
    qtm_version=QTM_DEFAULT_VERSION,
    on_state_changed=None,
    on_error=None
):
    try:
        LOG.debug("init_link enter")
        link = Link(qtm_host, qtm_port, on_state_changed, on_error)
        link.conn = await qtm.connect(
            host=qtm_host,
            port=qtm_port,
            version=qtm_version,
            on_event=link.on_event,
            on_disconnect=link.on_disconnect,
        )
        if link.conn is None:
            msg = "Failed to connect to QTM on '{}:{}' with protocol version '{}'" \
                .format(qtm_host, qtm_port, qtm_version)
            raise LinkError(msg)
        link.set_state(State.WAITING)
    finally:
        LOG.debug("init_link exit")
    return link
