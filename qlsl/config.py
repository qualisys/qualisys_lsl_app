"""
    Handle QTM parameters, LSL metadata, and conversion from QTM data to LSL data.
"""

import logging
import xml.etree.ElementTree as ET

from pylsl import cf_float32, StreamInfo
from qtm_rt.packet import QRTComponentType

LOG = logging.getLogger("qlsl")
LOG.setLevel(logging.DEBUG)

class Config:
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
    
    def cameras(self):
        try:
            return self.general["cameras"]
        except KeyError:
            return []
    
    def camera_count(self):
        return len(self.cameras())

    def channel_count(self):
        return 3*self.marker_count() + 6*self.body_count()

def parse_qtm_parameters(xml_string):
    xml = ET.fromstring(xml_string)
    config = Config()
    xml_general = xml.find("./General")
    if xml_general:
        config.general = parse_qtm_parameters_general(xml_general)
    xml_3d = xml.find("./The_3D")
    if xml_3d:
        config.the_3d = parse_qtm_parameters_3d(xml_3d)
    xml_6d = xml.find("./The_6D")
    if xml_6d:
        config.the_6d = parse_qtm_parameters_6d(xml_6d)
    return config

def parse_qtm_parameters_general(xml_general):
    frequency = None
    cameras = []
    xml_cameras = xml_general.findall("./Camera")
    for xml_camera in xml_cameras:
        camera = {}
        for xml_camera_param in xml_camera:
            tag = xml_camera_param.tag.lower()
            if tag in ["id", "model", "serial", "mode", "video_frequency"]:
                camera[tag] = xml_camera_param.text
            elif tag == "position":
                position = {}
                for xml_camera_pos_param in xml_camera_param:
                    tag = xml_camera_pos_param.tag.lower()
                    if tag in ["x", "y", "z"]:
                        position[tag] = float(xml_camera_pos_param.text)
                camera["position"] = position
        cameras.append(camera)
    frequency = float(xml_general.findtext("./Frequency"))
    return {
        "frequency": frequency,
        "cameras": cameras,
    }

def parse_qtm_parameters_3d(xml_3d):
    xml_label_names = xml_3d.findall("./Label/Name")
    markers = []
    for xml_name in xml_label_names:
        markers.append(xml_name.text)
    return {
        "markers": markers,
    }

def parse_qtm_parameters_6d(xml_6d):
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
                        point[tag] = float(xml_point_param.text)
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

def mm_to_m(mm):
    return round(mm/1000, 6)

# Note: 
# Changes in channel metadata should be reflected in qtm_packet_to_lsl_sample,
# and vice versa. 

def qtm_packet_to_lsl_sample(packet):
    sample = []
    if QRTComponentType.Component3d in packet.components:
        _, markers = packet.get_3d_markers()
        for marker in markers:
            sample.append(mm_to_m(marker.x))
            sample.append(mm_to_m(marker.y))
            sample.append(mm_to_m(marker.z))
    if QRTComponentType.Component6dEuler in packet.components:
        _, bodies = packet.get_6d_euler()
        for position, rotation in bodies:
            sample.append(mm_to_m(position.x))
            sample.append(mm_to_m(position.y))
            sample.append(mm_to_m(position.z))
            sample.append(rotation.a1)
            sample.append(rotation.a2)
            sample.append(rotation.a3)
    return sample

def new_lsl_stream_info(config, qtm_host, qtm_port):
    info = StreamInfo(
        name="Qualisys",
        type="Mocap",
        channel_count=config.channel_count(),
        channel_format=cf_float32,
        source_id="{}:{}".format(qtm_host, qtm_port),
    )
    channels = info.desc().append_child("channels")
    setup = info.desc().append_child("setup")
    markers = setup.append_child("markers")
    objects = setup.append_child("objects")
    cameras = setup.append_child("cameras")
    lsl_stream_info_add_markers(config, channels, markers)
    lsl_stream_info_add_6dof(config, channels, objects)
    lsl_stream_info_add_cameras(config, cameras)
    info.desc().append_child("acquisition") \
        .append_child_value("manufacturer", "Qualisys") \
        .append_child_value("model", "Qualisys Track Manager")
    return info

def lsl_stream_info_add_markers(config, channels, markers):
    def append_channel(marker, component, ch_type, unit):
        label = "{}_{}".format(marker, component)
        channels.append_child("channel") \
            .append_child_value("label", label) \
            .append_child_value("marker", marker) \
            .append_child_value("type", ch_type) \
            .append_child_value("unit", unit)
    def append_position_channel(marker, component):
        ch_type = "Position" + component
        append_channel(marker, component, ch_type, "meters")
    for marker in config.markers():
        markers.append_child("marker") \
            .append_child_value("label", marker)
        append_position_channel(marker, "X")
        append_position_channel(marker, "Y")
        append_position_channel(marker, "Z")

def lsl_stream_info_add_6dof(config, channels, objects):
    def append_channel(body, component, ch_type, unit):
        label = "{}_{}".format(body, component)
        channels.append_child("channel") \
            .append_child_value("label", label) \
            .append_child_value("object", body) \
            .append_child_value("type", ch_type) \
            .append_child_value("unit", unit)
    euler_angle_to_component = {
        "pitch": "P",
        "roll": "R",
        "yaw": "H",
    }
    def append_position_channel(body, component):
        ch_type = "Position" + component
        append_channel(body, component, ch_type, "meters")
    def append_orientation_channel(body, angle):
        component = euler_angle_to_component[angle.lower()]
        ch_type = "Orientation" + component
        append_channel(body, component, ch_type, "degrees")
    for body in config.bodies():
        name = body["name"]
        objects.append_child("object") \
            .append_child_value("class", "Rigid") \
            .append_child_value("label", name)
        append_position_channel(name, "X")
        append_position_channel(name, "Y")
        append_position_channel(name, "Z")
        angles = config.the_6d["euler"]
        append_orientation_channel(name, angles["first"])
        append_orientation_channel(name, angles["second"])
        append_orientation_channel(name, angles["third"])

def lsl_stream_info_add_cameras(config, cameras):
    def fmt_pos(pos):
        return str(mm_to_m(pos))
    for camera in config.cameras():
        info = cameras.append_child("cameras") \
            .append_child_value("label", camera["id"])
        if "position" in camera:
            pos = camera["position"]
            info.append_child("position") \
                .append_child_value("X", fmt_pos(pos["x"])) \
                .append_child_value("Y", fmt_pos(pos["y"])) \
                .append_child_value("Z", fmt_pos(pos["z"]))
