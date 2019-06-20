"""
    Stream 3D and 6D data from QTM and pass it through an LSL outlet.
"""

import asyncio
import logging
import time
import qtm
from qtm.packet import QRTComponentType
import xml.etree.ElementTree as ET

from pylsl import cf_float32, StreamInfo, StreamOutlet

LOG = logging.getLogger("qlsl")
QTM_DEFAULT_PORT = 22223

class QLSLStream:
    def __init__(self, qtm_host, qtm_port, qtm_params):
        self.qtm_host = qtm_host
        self.qtm_port = qtm_port
        self.qtm_params = qtm_params
        self.packet_count = 0
    
    def channel_count(self):
        return 3*self.marker_count()
    
    def marker_count(self):
        return len(self.qtm_params["markers"])
    
    # TODO: tests
    def init_stream_info(self):
        info = StreamInfo(name="Qualisys",
            type="Mocap",
            channel_count=self.channel_count(),
            channel_format=cf_float32,
            source_id="{}:{}".format(self.qtm_host, self.qtm_port),
        )
        channels = info.desc().append_child("channels")
        def append_channel(marker, axis):
            channels.append_child("channel") \
                .append_child_value("label", "{}_{}".format(marker, axis)) \
                .append_child_value("marker", marker) \
                .append_child_value("type", "Position" + axis) \
                .append_child_value("unit", "millimeters")
        for marker in self.qtm_params["markers"]:
            append_channel(marker, "X")
            append_channel(marker, "Y")
            append_channel(marker, "Z")
        info.desc().append_child("acquisition") \
            .append_child_value("model", "Qualisys")
        self.info = info
    
    def open_stream_outlet(self):
        self.outlet = StreamOutlet(self.info, 32, 360)
    
    # TODO: tests
    def qtm_packet_to_lsl_sample(self, packet):
        if QRTComponentType.Component3d not in packet.components:
            return
        header, markers = packet.get_3d_markers()
        if header.marker_count != self.marker_count():
            raise RuntimeError("Expected {} but got {} markers from QTM packet".format(self.marker_count(), header.marker_count))
        sample = []
        for marker in markers:
            sample.append(marker.x)
            sample.append(marker.y)
            sample.append(marker.z)
        return sample

    def on_qtm_packet(self, packet):
        self.packet_count += 1
        sample = self.qtm_packet_to_lsl_sample(packet)
        if len(sample) > 0:
            self.outlet.push_sample(sample)
    
async def qtm_receiver(queue, lsl):
    LOG.debug("qtm_receiver enter")
    try:
        while True:
            packet = await queue.get()
            if packet is None:
                break
            lsl.on_qtm_packet(packet)
    finally:
        LOG.debug("qtm_receiver exit")

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

class Context:
    def __init__(self, qtm, lsl, receiver, queue):
        self.qtm = qtm
        self.lsl = lsl
        self.receiver = receiver
        self.queue = queue

class LinkError(Exception):
    pass

async def link(
    qtm_host,
    qtm_port=QTM_DEFAULT_PORT,
    qtm_on_event=None,
    qtm_on_disconnect=None
):
    LOG.debug("link enter")

    qtm_conn = await qtm.connect(
        host=qtm_host, port=qtm_port,
        on_event=qtm_on_event, on_disconnect=qtm_on_disconnect,
    )
    if qtm_conn is None:
        raise LinkError("Failed to connect to QTM on '{}:{}'".format(qtm_host, qtm_port))
    
    packet = await qtm_conn.get_parameters(parameters=["general", "3d", "6d"])
    qtm_params = xml_parse_parameters(packet.decode("utf-8"))
    if len(qtm_params["markers"]) == 0:
        qtm_conn.disconnect()
        raise LinkError("Zero markers")

    lsl = QLSLStream(qtm_host, qtm_port, qtm_params)
    lsl.init_stream_info()
    lsl.open_stream_outlet()

    queue = asyncio.Queue()
    receiver = asyncio.ensure_future(qtm_receiver(queue, lsl))

    await qtm_conn.stream_frames(components=["3d"], on_packet=queue.put_nowait)

    LOG.debug("link exit")

    return Context(
        qtm=qtm_conn,
        lsl=lsl,
        receiver=receiver,
        queue=queue,
    )

async def unlink(ctx):
    LOG.debug("unlink enter")
    ctx.queue.put_nowait(None)
    await ctx.receiver
    await ctx.qtm.stream_frames_stop()
    ctx.qtm.disconnect()
    LOG.debug("unlink exit")
