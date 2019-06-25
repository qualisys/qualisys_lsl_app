"""
    Stream 3D and 6DOF data from QTM and pass it through an LSL outlet.
"""

import asyncio
from enum import Enum
import logging
import time

from pylsl import StreamInfo, StreamOutlet
import qtm
from qtm import QRTEvent

from qlsl.config import (
    Config,
    new_lsl_stream_info,
    parse_qtm_parameters,
    qtm_packet_to_lsl_sample,
)

LOG = logging.getLogger("qlsl")
QTM_DEFAULT_PORT = 22223
QTM_DEFAULT_VERSION = "1.19"

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

        self.state = State.INITIAL
        self.conn = None
        self.packet_count = 0
        self.reset_stream_context()
    
    def reset_stream_context(self):
        self.config = Config()
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
        if self.conn:
            self.err_disconnect("Disconnected from QTM")
        if exc:
            LOG.debug("link::on_disconnect: {}".format(exc))

    def open_lsl_stream_outlet(self):
        self.lsl_info = new_lsl_stream_info(self.config, self.host, self.port)
        self.lsl_outlet = StreamOutlet(info=self.lsl_info, max_buffered=180)
    
    def err_disconnect(self, err_msg):
        asyncio.ensure_future(self.shutdown(err_msg))

    async def shutdown(self, err_msg=None):
        try:
            LOG.debug("shutdown enter")
            if not self.is_stopped():
                if self.state == State.STREAMING:
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
            LOG.debug("get_state exception: {}".format(ex))
            self.err_disconnect("QTM error: {}".format(ex))

    async def stop_stream(self):
        if self.conn and self.conn.has_transport():
            try:
                await self.conn.stream_frames_stop()
            except qtm.QRTCommandException as ex:
                LOG.debug("stream_frames_stop exception: {}".format(ex))
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
            config = parse_qtm_parameters(packet.decode("utf-8"))
            if config.marker_count() == 0 or config.body_count() == 0:
                self.err_disconnect("No 3D or 6DOF data available from QTM")
                LOG.debug("marker_count {} body_count {}".format(
                    config.marker_count(), config.body_count()
                ))
                return
            self.config = config
            self.receiver_queue = asyncio.Queue()
            self.receiver_task = asyncio.ensure_future(self.stream_receiver())
            self.open_lsl_stream_outlet()
            await self.conn.stream_frames(
                components=["3d", "6deuler"],
                on_packet=self.receiver_queue.put_nowait,
            )
            self.set_state(State.STREAMING)
        except qtm.QRTCommandException as ex:
            LOG.debug("stream_frames exception: {}".format(ex))
            self.err_disconnect("QTM error: {}".format(ex))
        except Exception as ex:
            LOG.debug("link::start_stream exception: {}".format(ex))
            self.err_disconnect("Internal error: {}".format(ex))
            raise ex

    async def stream_receiver(self):
        try:
            LOG.debug("link::stream_receiver enter")
            while True:
                packet = await self.receiver_queue.get()
                if packet is None:
                    break
                sample = qtm_packet_to_lsl_sample(packet)
                if len(sample) != self.config.channel_count():
                    self.err_disconnect("Stream canceled: \
                        QTM stream data inconsistent with LSL metadata")
                else:
                    self.packet_count += 1
                    self.lsl_outlet.push_sample(sample)
        finally:
            LOG.debug("link::stream_receiver exit")

class LinkError(Exception):
    pass

async def init(
    qtm_host,
    qtm_port=QTM_DEFAULT_PORT,
    qtm_version=QTM_DEFAULT_VERSION,
    on_state_changed=None,
    on_error=None
):
    try:
        LOG.debug("link::init enter")
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
    except LinkError:
        raise
    except Exception as ex:
        LOG.debug("link::init exception: {}".format(ex))
        raise LinkError("Internal error: {}".format(ex))
    finally:
        LOG.debug("link::init exit")
    return link
