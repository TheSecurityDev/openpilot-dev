import cv2
import numpy as np

from msgq.visionipc import VisionIpcServer, VisionStreamType
from cereal import messaging

from openpilot.tools.sim.lib.common import W, H
from openpilot.system.camerad.cameras.nv12_info import get_nv12_info

_nv12_stride, _nv12_y_height, _nv12_uv_height, _nv12_buf_size = get_nv12_info(W, H)
_nv12_uv_offset = _nv12_stride * _nv12_y_height


def rgb_to_nv12(rgb, out=None):
  """Convert RGB image to VENUS-aligned NV12 (YUV420) using cv2.
  The warp model reads NV12 with VENUS stride and uv_offset from get_nv12_info,
  so the buffer layout must match (stride-padded rows, UV at uv_offset)."""
  h, w = rgb.shape[:2]
  if out is None:
    out = np.zeros(_nv12_buf_size, dtype=np.uint8)
  # cv2 outputs I420 (planar): [Y: h*w] [U: h/2*w/2] [V: h/2*w/2]
  flat = cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420).ravel()
  y_size = h * w
  uv_size = h // 2 * w // 2
  y_plane = out[:_nv12_y_height * _nv12_stride].reshape(_nv12_y_height, _nv12_stride)
  y_plane[:h, :w] = flat[:y_size].reshape(h, w)
  uv_plane = out[_nv12_uv_offset:_nv12_uv_offset + _nv12_uv_height * _nv12_stride].reshape(_nv12_uv_height, _nv12_stride)
  uv_plane[:h // 2, :w:2] = flat[y_size:y_size + uv_size].reshape(h // 2, w // 2)
  uv_plane[:h // 2, 1:w:2] = flat[y_size + uv_size:].reshape(h // 2, w // 2)
  return out


class Camerad:
  """Simulates the camerad daemon"""
  def __init__(self, dual_camera):
    self.pm = messaging.PubMaster(['roadCameraState', 'wideRoadCameraState'])

    self.frame_road_id = 0
    self.frame_wide_id = 0
    self.vipc_server = VisionIpcServer("camerad")

    self.vipc_server.create_buffers_with_sizes(VisionStreamType.VISION_STREAM_ROAD, 5, W, H,
                                               _nv12_buf_size, _nv12_stride, _nv12_uv_offset)
    if dual_camera:
      self.vipc_server.create_buffers_with_sizes(VisionStreamType.VISION_STREAM_WIDE_ROAD, 5, W, H,
                                                 _nv12_buf_size, _nv12_stride, _nv12_uv_offset)

    self.vipc_server.start_listener()

    # Preallocated VENUS-aligned NV12 buffers to avoid per-frame allocation
    self._nv12_road = np.zeros(_nv12_buf_size, dtype=np.uint8)
    self._nv12_wide = np.zeros(_nv12_buf_size, dtype=np.uint8) if dual_camera else None

  def cam_send_yuv_road(self, yuv):
    self._send_yuv(yuv, self.frame_road_id, 'roadCameraState', VisionStreamType.VISION_STREAM_ROAD)
    self.frame_road_id += 1

  def cam_send_yuv_wide_road(self, yuv):
    self._send_yuv(yuv, self.frame_wide_id, 'wideRoadCameraState', VisionStreamType.VISION_STREAM_WIDE_ROAD)
    self.frame_wide_id += 1

  def rgb_to_yuv(self, rgb, buf=None):
    """Convert RGB to NV12 YUV format."""
    assert rgb.shape == (H, W, 3), f"{rgb.shape}"
    assert rgb.dtype == np.uint8
    return rgb_to_nv12(rgb, buf)

  def _send_yuv(self, yuv, frame_id, pub_type, yuv_type):
    eof = int(frame_id * 0.05 * 1e9)
    self.vipc_server.send(yuv_type, yuv, frame_id, eof, eof)

    dat = messaging.new_message(pub_type, valid=True)
    msg = {
      "frameId": frame_id,
      "transform": [1.0, 0.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.0, 0.0, 1.0]
    }
    setattr(dat, pub_type, msg)
    self.pm.send(pub_type, dat)
