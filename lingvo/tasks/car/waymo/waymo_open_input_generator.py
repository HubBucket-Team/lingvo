# Lint as: python2, python3
# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Input generator for waymo open dataset (WaymoOD)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from lingvo import compat as tf
from lingvo.core import datasource
from lingvo.core import hyperparams
from lingvo.core import py_utils
from lingvo.tasks.car import input_extractor
from lingvo.tasks.car import input_preprocessors

import numpy as np


def _Dense(sparse, default_value=0):
  return tf.sparse_to_dense(
      sparse_indices=sparse.indices,
      output_shape=sparse.dense_shape,
      sparse_values=sparse.values,
      default_value=default_value)


def _NestedMapToParams(nmap):
  p = hyperparams.Params()
  for k, v in nmap.FlattenItems():
    p.Define(k, v, '')
  return p


class WaymoFrameMetadataExtractor(input_extractor.FieldsExtractor):
  """Extracts per frame metadata from a WaymoOD tf.Example.

  Emits:
    pose: [4, 4] - A float Tensor with the 4x4 transformation matrix for
    converting from "world" coordinates SDC coordinates.

    run_segment: string scalar - The run segment identifier.

    run_start_offset: int64 scalar - Offset of this scene from the start of the
    run segment (in microseconds).

    time_of_day: string scalar - Categorical description of time of day,
    e.g., "Day".

    location: string scalar - Categorical description of geographical location,
    e.g., "location_sf".

    weather: string scalar - Categorical description of weather of scene,
    e.g., "sunny".
  """

  # Valid options for metadata that we can use for validation
  # Filters that aren't in this list will still be allowed, but these will
  # be checked for extra safety.
  VALIDATED_FILTER_OPTIONS = py_utils.NestedMap(
      time_of_day=['Day', 'Dawn/Dusk', 'Night'],
      weather=['rain', 'sunny'],
      location=['location_sf', 'location_phx', 'location_other'])

  @classmethod
  def Params(cls):
    p = super(WaymoFrameMetadataExtractor, cls).Params()
    p.Define(
        'equality_filters', None, 'A list of tuples(str, list) '
        'where each first value is a metadata key (e.g. `weather`) '
        'and the second value is a list of valid values to filter for. '
        'Each filter will check whether the value of a given example '
        'for that metadata key matches one of the allowed filter values. '
        'Then the result of each filter (each tuple) will be AND-ed '
        'together. Example usage would be: '
        '[("location", ["location_sf"]), ("weather", ["sunny"])] '
        'Which would only allow through examples that are in SF '
        'AND have sunny weather. ')
    return p

  def _ValidateFilterValues(self):
    """Check the filter against several blessed values."""
    p = self.params
    for filter_key, filter_values in p.equality_filters:
      # Type check
      if (not isinstance(filter_key, str) or
          not isinstance(filter_values, list)):
        raise ValueError('Each element in `equality_filters` must be a '
                         'tuple of (str, list).')
      # If it's not one of the "blessed" validated options, just let it through
      if filter_key not in self.VALIDATED_FILTER_OPTIONS:
        continue

      # If we do know its valid options, check each value against this list
      valid_options = self.VALIDATED_FILTER_OPTIONS[filter_key]
      for filter_value in filter_values:
        if filter_value not in valid_options:
          raise ValueError(
              'Filter {} value: {} not in valid options: {}'.format(
                  filter_key, filter_value, valid_options))

  def __init__(self, params):
    super(WaymoFrameMetadataExtractor, self).__init__(params)
    p = self.params

    if p.equality_filters:
      if not isinstance(p.equality_filters, list):
        raise ValueError('`equality_filters` param must be a list.')
      if not all([isinstance(val, tuple) for val in p.equality_filters]):
        raise ValueError('Every item in `equality_filters` must be a tuple.')
      self._ValidateFilterValues()

  def FeatureMap(self):
    """Return a dictionary from tf.Example feature names to Features."""
    feature_map = {}
    feature_map['pose'] = tf.VarLenFeature(dtype=tf.float32)
    feature_map['run_segment'] = tf.FixedLenFeature((), tf.string, '')
    feature_map['run_start_offset'] = tf.FixedLenFeature((), tf.int64, 0)
    feature_map['time_of_day'] = tf.FixedLenFeature((), tf.string, '')
    feature_map['location'] = tf.FixedLenFeature((), tf.string, '')
    feature_map['weather'] = tf.FixedLenFeature((), tf.string, '')
    return feature_map

  def _Extract(self, features):
    """Extract data into Tensor format."""
    vehicle_pose = tf.reshape(_Dense(features['pose']), [4, 4])
    run_segment = features['run_segment']
    run_start_offset = features['run_start_offset']
    time_of_day = features['time_of_day']
    location = features['location']
    weather = features['weather']
    return py_utils.NestedMap(
        pose=vehicle_pose,
        run_segment=run_segment,
        run_start_offset=run_start_offset,
        time_of_day=time_of_day,
        location=location,
        weather=weather)

  def Shape(self):
    """The expected shape of each field."""
    return py_utils.NestedMap(
        pose=tf.TensorShape([4, 4]),
        run_segment=tf.TensorShape([]),
        run_start_offset=tf.TensorShape([]),
        time_of_day=tf.TensorShape([]),
        location=tf.TensorShape([]),
        weather=tf.TensorShape([]))

  def DType(self):
    """The Dtype of each field."""
    return py_utils.NestedMap(
        pose=tf.float32,
        run_segment=tf.string,
        run_start_offset=tf.int64,
        time_of_day=tf.string,
        location=tf.string,
        weather=tf.string)

  def Filter(self, outputs):
    """Optionally filters the data based on context info."""
    p = self.params
    if p.equality_filters is None:
      return 1

    allowed_example = tf.convert_to_tensor(True)
    for filter_key, filter_values in p.equality_filters:
      if filter_key not in outputs:
        raise ValueError(
            'Filter key `{}` not found in extracted data.'.format(filter_key))
      has_allowed_data = tf.reduce_any(
          tf.equal(outputs[filter_key], filter_values))
      allowed_example = tf.logical_and(allowed_example, has_allowed_data)

    not_allowed_example = 1 - tf.to_int32(allowed_example)
    return 1 + (not_allowed_example * input_extractor.BUCKET_UPPER_BOUND)


class WaymoImageExtractor(input_extractor.FieldsExtractor):
  """Extracts the camera image data from a WaymoOD tf.Example.

   Emits:
    images: [height, width, 3] - Images from the corresponding cameras.
    The cameras are [FRONT, FRONT_LEFT, FRONT_RIGHT, SIDE_LEFT, SIDE_RIGHT].

    intrinsics: [9] - Instrinsics of the camera.

    extrinsics: [4, 4] - Extrinsics of the camera

    pose: [4, 4] - Pose of the camera when the corresponding image is taken.

    velocity: [6] - Velocity of the camera when the corresponding image is
    taken. The first three numbers (vx, vy, vz) are velocities in world frame,
    in m/s. The last three numbers (roll, pitch, yaw) are the rotation rates
    in vehicle frame, in rad/s.
  """

  @classmethod
  def Params(cls):
    p = super(WaymoImageExtractor, cls).Params()
    p.Define('camera_names',
             ['FRONT', 'FRONT_LEFT', 'FRONT_RIGHT', 'SIDE_LEFT', 'SIDE_RIGHT'],
             'The names of the cameras from which images will be extracted.')
    p.Define('image_shape', [1280, 1920, 3],
             'The shape that images are cropped to.')
    return p

  def FeatureMap(self):
    """Return a dictionary from tf.Example feature names to Features."""
    p = self.params
    features = {}
    features['pose'] = tf.VarLenFeature(dtype=tf.float32)

    for camera_name in p.camera_names:
      features['image_%s' % camera_name] = tf.VarLenFeature(dtype=tf.string)
      features['image_%s_shape' % camera_name] = (
          tf.VarLenFeature(dtype=tf.int64))
      features['camera_%s_intrinsics' %
               camera_name] = tf.VarLenFeature(dtype=tf.float32)
      features['camera_%s_extrinsics' %
               camera_name] = tf.VarLenFeature(dtype=tf.float32)

      features['camera_%s_rolling_shutter_direction' %
               camera_name] = tf.FixedLenFeature(
                   dtype=tf.int64, shape=())
      features['image_%s_pose' %
               camera_name] = tf.VarLenFeature(dtype=tf.float32)
      features['image_%s_velocity' %
               camera_name] = tf.VarLenFeature(dtype=tf.float32)

      for feat in [
          'pose_timestamp', 'shutter', 'camera_trigger_time',
          'camera_readout_done_time'
      ]:
        features['image_%s_%s' % (camera_name, feat)] = tf.FixedLenFeature(
            dtype=tf.float32, shape=())
    return features

  def _Extract(self, features):
    """Returns the image Tensor."""
    outputs = {}
    p = self.params

    outputs['frame_pose'] = tf.reshape(_Dense(features['pose']), [4, 4])

    for camera_name in p.camera_names:
      image_shape = tf.reshape(
          _Dense(features['image_%s_shape' % camera_name]), [-1])
      image = tf.io.decode_png(
          tf.strings.reduce_join(
              _Dense(features['image_%s' % camera_name], default_value='')))
      image = tf.reshape(image, image_shape)
      image = py_utils.PadOrTrimTo(image, p.image_shape)
      intrinsics = tf.reshape(
          _Dense(features['camera_%s_intrinsics' % camera_name]), [9])
      extrinsics = tf.reshape(
          _Dense(features['camera_%s_extrinsics' % camera_name]), [4, 4])
      pose = tf.reshape(_Dense(features['image_%s_pose' % camera_name]), [4, 4])
      velocity = tf.reshape(
          _Dense(features['image_%s_velocity' % camera_name]), [6])
      outputs['%s' % camera_name] = image
      outputs['%s_intrinsics' % camera_name] = intrinsics
      outputs['%s_extrinsics' % camera_name] = extrinsics
      outputs['%s_pose' % camera_name] = pose
      outputs['%s_velocity' % camera_name] = velocity

      outputs['%s_rolling_shutter_direction' %
              camera_name] = features['camera_%s_rolling_shutter_direction' %
                                      camera_name]

      for feat in [
          'shutter', 'camera_trigger_time', 'camera_readout_done_time',
          'pose_timestamp'
      ]:
        outputs['%s_%s' % (camera_name, feat)] = features['image_%s_%s' %
                                                          (camera_name, feat)]

    return py_utils.NestedMap(outputs)

  def Shape(self):
    """Shape of images."""
    p = self.params
    shapes = {'frame_pose': tf.TensorShape([4, 4])}

    for camera_name in p.camera_names:
      shapes['%s' % camera_name] = tf.TensorShape(p.image_shape)
      # 1d Array of [f_u, f_v, c_u, c_v, k{1, 2}, p{1, 2}, k{3}].
      # Note that this intrinsic corresponds to the images after scaling.
      # Camera model: pinhole camera.
      # Lens distortion:
      # Radial distortion coefficients: k1, k2, k3.
      # Tangential distortion coefficients: p1, p2.
      # k_{1, 2, 3}, p_{1, 2} follows the same definition as OpenCV.
      shapes['%s_intrinsics' % camera_name] = tf.TensorShape([9])
      shapes['%s_extrinsics' % camera_name] = tf.TensorShape([4, 4])
      shapes['%s_pose' % camera_name] = tf.TensorShape([4, 4])
      shapes['%s_velocity' % camera_name] = tf.TensorShape([6])
      for feat in [
          'pose_timestamp', 'shutter', 'camera_trigger_time',
          'camera_readout_done_time'
      ]:
        shapes['%s_%s' % (camera_name, feat)] = tf.TensorShape([])
      shapes['%s_rolling_shutter_direction' % camera_name] = tf.TensorShape([])

    return py_utils.NestedMap(shapes)

  def DType(self):
    """Dtypes of images."""
    p = self.params
    dtypes = {'frame_pose': tf.float32}
    for camera_name in p.camera_names:
      dtypes['%s' % camera_name] = tf.uint8
      dtypes['%s_intrinsics' % camera_name] = tf.float32
      dtypes['%s_extrinsics' % camera_name] = tf.float32
      dtypes['%s_pose' % camera_name] = tf.float32
      dtypes['%s_velocity' % camera_name] = tf.float32
      for feat in [
          'pose_timestamp', 'shutter', 'camera_trigger_time',
          'camera_readout_done_time'
      ]:
        dtypes['%s_%s' % (camera_name, feat)] = tf.float32
      dtypes['%s_rolling_shutter_direction' % camera_name] = tf.int64
    return py_utils.NestedMap(dtypes)


class WaymoLaserExtractor(input_extractor.LaserExtractor):
  """Extracts the raw laser data from a WaymoOD tf.Example."""

  @classmethod
  def Params(cls):
    p = super(WaymoLaserExtractor, cls).Params().Set(
        max_num_points=None, num_features=3)
    p.Define('lidar_names', ['TOP', 'SIDE_LEFT', 'SIDE_RIGHT', 'FRONT', 'REAR'],
             'The names of the lidars from which lasers will be extracted.')
    return p

  def FeatureMap(self):
    """Return a dictionary from tf.Example feature names to Features."""
    p = self.params
    features = {}
    for lidar in p.lidar_names:
      for ri in ['ri1', 'ri2']:
        features['laser_%s_%s' %
                 (lidar, ri)] = tf.VarLenFeature(dtype=tf.float32)
    return features

  def _Extract(self, features):
    """Returns the laser Tensor."""
    p = self.params
    all_xyzs = []
    all_laser_features = []

    for lidar in p.lidar_names:
      for ri in ['ri1', 'ri2']:
        feature_name = 'laser_%s_%s' % (lidar, ri)
        laser_data = tf.reshape(
            _Dense(features[feature_name]), [-1, 3 + p.num_features])
        points_xyz = laser_data[..., 0:3]
        points_feature = laser_data[..., 3:]

        all_xyzs += [points_xyz]
        all_laser_features += [points_feature]

    # Stack all of the points along the major dimension
    points_xyz = tf.concat(all_xyzs, axis=0)
    points_feature = tf.concat(all_laser_features, axis=0)

    if p.max_num_points is not None:
      npoints = tf.shape(points_xyz)[0]
      points_xyz = py_utils.PadOrTrimTo(points_xyz, [p.max_num_points, 3])
      points_feature = py_utils.PadOrTrimTo(points_feature,
                                            [p.max_num_points, p.num_features])
      points_padding = 1.0 - py_utils.PadOrTrimTo(
          tf.ones([npoints]), [p.max_num_points])

    ret = py_utils.NestedMap(
        points_xyz=points_xyz, points_feature=points_feature)
    if p.max_num_points is not None:
      ret.points_padding = points_padding
    return ret


class WaymoLabelExtractor(input_extractor.FieldsExtractor):
  """Extracts the bounding box and label info from a WaymoOD tf.Example.

  Emits:
    labels: [p.max_num_objects] - Integer label for each bounding box object
    corresponding to the index in car.open_dataset.Label.Type (shifted by 1 to
    have 0 represent the background class).

    label_ids: [p.max_num_objects] - String unique identifier for each labeled
    object on a per run_segment basis. This can be used for associating
    objects across frames (over time).

    detection_difficulties: [p.max_num_objects] - The per-box difficulty level
    for detection task as defined in car.open_dataset.Label.DifficultyLevel.

    tracking_difficulties: [p.max_num_objects] - The per-box difficulty level
    for tracking task as defined in car.open_dataset.Label.DifficultyLevel.

    bboxes_3d: [p.max_num_objects, 7] - 3D bounding box data in [x, y, z, l, w,
    h, heading] format. x, y, z are the object center in world coordinates;
    l, w, h are the dimensions of the box, and heading is the rotation angle
    around the z-axis. See car.open_dataset.Label.Box for definitions.

    bboxes_3d_mask: [p.max_num_objects] - Mask for all the above tensors (mask
    is the inversion of padding).

    bboxes_3d_num_points: [p.max_num_objects] - Integer for each box indicating
    how many points are in that ground truth box.

    unfiltered_bboxes_3d_mask: [p.max_num_objects] - The mask before filtering
    out bboxes whose labels are not in p.filter_labels.

    speed: [p.max_num_objects, 2] - The object speed in x, y.

    acceleration: [p.max_num_objects, 2] - The object acceleration in x, y.
  """

  @classmethod
  def Params(cls):
    p = super(WaymoLabelExtractor, cls).Params()
    p.Define('max_num_objects', 512,
             'Each frame may contain up to these many bbox.')
    p.Define(
        'filter_labels', [], 'Specifies a list of label '
        'indices to keep.  If empty, no filtering is done.')
    return p

  def FeatureMap(self):
    """Return a dictionary from tf.Example feature names to Features."""
    feature_map = {}
    feature_map['labels'] = tf.VarLenFeature(dtype=tf.int64)
    feature_map['label_ids'] = tf.VarLenFeature(dtype=tf.string)
    feature_map['detection_difficulties'] = tf.VarLenFeature(dtype=tf.int64)
    feature_map['tracking_difficulties'] = tf.VarLenFeature(dtype=tf.int64)
    feature_map['bboxes_3d'] = tf.VarLenFeature(dtype=tf.float32)
    feature_map['bboxes_3d_num_points'] = tf.VarLenFeature(dtype=tf.int64)
    feature_map['label_metadata'] = tf.VarLenFeature(dtype=tf.float32)
    return feature_map

  def _Extract(self, features):
    p = self.params
    # Label values match the proto enum car.open_dataset.Label.Type. The value
    # range is [1..4] for non-background labels.
    labels = tf.to_int32(_Dense(features['labels']))
    labels = py_utils.PadOrTrimTo(labels, [p.max_num_objects])
    label_ids = tf.reshape(_Dense(features['label_ids'], ''), [-1])
    label_ids = py_utils.PadOrTrimTo(label_ids, [p.max_num_objects], '')
    bboxes_3d = tf.reshape(_Dense(features['bboxes_3d']), [-1, 7])
    bboxes_3d_mask = tf.ones([tf.shape(bboxes_3d)[0]])
    bboxes_3d_num_points = tf.to_int32(_Dense(features['bboxes_3d_num_points']))
    bboxes_3d = py_utils.PadOrTrimTo(bboxes_3d, [p.max_num_objects, 7])
    bboxes_3d_mask = py_utils.PadOrTrimTo(bboxes_3d_mask, [p.max_num_objects])
    bboxes_3d_num_points = py_utils.PadOrTrimTo(bboxes_3d_num_points,
                                                [p.max_num_objects])
    label_metadata = tf.reshape(_Dense(features['label_metadata']), [-1, 4])
    label_metadata = py_utils.PadOrTrimTo(label_metadata,
                                          [p.max_num_objects, 4])

    detection_difficulties = py_utils.PadOrTrimTo(
        tf.to_int32(_Dense(features['detection_difficulties'])),
        [p.max_num_objects])
    tracking_difficulties = py_utils.PadOrTrimTo(
        tf.to_int32(_Dense(features['tracking_difficulties'])),
        [p.max_num_objects])
    unfiltered_bboxes_3d_mask = bboxes_3d_mask

    if p.filter_labels:
      valid_labels = tf.constant([p.filter_labels])
      bbox_mask = tf.reduce_any(
          tf.equal(tf.expand_dims(labels, 1), valid_labels), axis=1)
      bboxes_3d_mask *= tf.to_float(bbox_mask)

    outputs = {
        'labels': labels,
        'label_ids': label_ids,
        'detection_difficulties': detection_difficulties,
        'tracking_difficulties': tracking_difficulties,
        'bboxes_3d': bboxes_3d,
        'bboxes_3d_mask': bboxes_3d_mask,
        'bboxes_3d_num_points': bboxes_3d_num_points,
        'unfiltered_bboxes_3d_mask': unfiltered_bboxes_3d_mask,
        'speed': label_metadata[:, :2],
        'acceleration': label_metadata[:, 2:],
    }

    return py_utils.NestedMap(outputs)

  def Shape(self):
    """Shape of BBoxes."""
    p = self.params
    shapes = {
        'labels': tf.TensorShape([p.max_num_objects]),
        'label_ids': tf.TensorShape([p.max_num_objects]),
        'detection_difficulties': tf.TensorShape([p.max_num_objects]),
        'tracking_difficulties': tf.TensorShape([p.max_num_objects]),
        'bboxes_3d': tf.TensorShape([p.max_num_objects, 7]),
        'bboxes_3d_mask': tf.TensorShape([p.max_num_objects]),
        'bboxes_3d_num_points': tf.TensorShape([p.max_num_objects]),
        'unfiltered_bboxes_3d_mask': tf.TensorShape([p.max_num_objects]),
        'speed': tf.TensorShape([p.max_num_objects, 2]),
        'acceleration': tf.TensorShape([p.max_num_objects, 2])
    }
    return py_utils.NestedMap(shapes)

  def DType(self):
    """Dtypes of BBoxes."""
    dtypes = py_utils.NestedMap()
    dtypes.labels = tf.int32
    dtypes.label_ids = tf.string
    dtypes.detection_difficulties = tf.int32
    dtypes.tracking_difficulties = tf.int32
    dtypes.bboxes_3d = tf.float32
    dtypes.bboxes_3d_mask = tf.float32
    dtypes.bboxes_3d_num_points = tf.int32
    dtypes.unfiltered_bboxes_3d_mask = tf.float32
    dtypes.speed = tf.float32
    dtypes.acceleration = tf.float32
    return dtypes


class RangeImageExtractor(input_extractor.FieldsExtractor):
  """Extracts the range images from a Waymo OD tf.Example.

  The outputs contain the following:

  Let ri_shape = [H, W] of the corresponding range image.

  - For every short range laser (params.cbr_laser_names):
      For every return (params.returns):
        $LASERNAME_RETURN:
          .xyz - tf.float32 of ri_shape + [3]

          .features - tf.float32 of ri_shape + [4]

          .mask - tf.float32 of ri_shape indicating whether the laser
          xyz and feature at each coordinate is real or padded. A coordinate
          has a real point iff the mask is set to 1.

      $LASERNAME_beam_inclinations: tf.float32 [2] listing the
      min and max beam inclinations.

      $LASERNAME_extrinsics: tf.float32 [4, 4] extrinsics matrix.

  - For every longer range laser (params.gbr_laser_names):
      For every return (params.returns):
          .xyz: tf.float32 of ri_shape + [3]
          .features: tf.float32 of ri_shape + [4]
          .mask: tf.float32 of ri_shape

      $LASERNAME_beam_inclinations: tf.float32 [64] listing the
      non-uniform beam inclinations for the longer range laser.

      $LASERNAME_extrinsics: tf.float32 [4, 4] extrinsics matrix

  On laser returns:
    ri1 and ri2 are the first and second returns of the sensors.

  On laser sensors:
    If there are 5 total sensors, there will be 5 * len(returns)
    outputs.

  The last dimension of range image is 4, indicating the following
  features:

    range: (if entry is -1, it means there is no laser value there).

    intensity

    elongation

    1. if laser point entry in 'no label zone', 0. otherwise.

  The xyz range image output is a [H, W, 3] Tensor indicating the cartesian
  coordinates corresponding to each range image pixel in the range image.  One
  should use the mask computed from the 'range' channel of the range image to
  select only the points that exist.
  """

  @classmethod
  def Params(cls):
    p = super(RangeImageExtractor, cls).Params()
    p.Define(
        'cbr_laser_names', ['SIDE_LEFT', 'FRONT', 'REAR', 'SIDE_RIGHT'],
        'The names of the CBR sensors from which range images '
        'will be extracted.')
    p.Define('cbr_ri_shape', [200, 600, 4], 'Shape of each CBR range image.')
    p.Define(
        'gbr_laser_names', ['TOP'],
        'The names of the GBR sensors from which range images '
        'will be extracted.')
    p.Define(
        'returns', ['ri1', 'ri2'],
        'The names of the laser returns to export.  E.g., ri1 is '
        'the first return, ri2 is the second return.')
    p.Define('gbr_ri_shape', [64, 2650, 4], 'Shape of each GBR range image.')
    return p

  def FeatureMap(self):
    """Return a dictionary from tf.Example feature names to Features."""
    p = self.params
    feature_map = {}
    for laser in p.cbr_laser_names + p.gbr_laser_names:
      feature_map['%s_beam_inclinations' % laser] = (
          tf.VarLenFeature(dtype=tf.float32))
      feature_map['%s_beam_inclination_min' % laser] = (
          tf.VarLenFeature(dtype=tf.float32))
      feature_map['%s_beam_inclination_max' % laser] = (
          tf.VarLenFeature(dtype=tf.float32))
      feature_map['%s_extrinsics' % laser] = tf.VarLenFeature(dtype=tf.float32)
      if laser in p.gbr_laser_names:
        feature_map['%s_pose' % laser] = tf.VarLenFeature(dtype=tf.float32)

      for returns in p.returns:
        feature_map['%s_%s' %
                    (laser, returns)] = tf.VarLenFeature(dtype=tf.float32)
        feature_map['%s_%s_shape' %
                    (laser, returns)] = tf.VarLenFeature(dtype=tf.int64)
    feature_map['pose'] = tf.VarLenFeature(dtype=tf.float32)
    return feature_map

  def _Extract(self, features):
    p = self.params
    ri_outputs = {}
    outputs = {}
    frame_pose = tf.reshape(_Dense(features['pose']), [4, 4])
    for laser in p.cbr_laser_names + p.gbr_laser_names:
      # Extract range images.
      for returns in p.returns:
        ri_shape = tf.reshape(
            _Dense(features['%s_%s_shape' % (laser, returns)]), [-1])
        range_image = tf.reshape(
            _Dense(features['%s_%s' % (laser, returns)]), ri_shape)

        shape_to_check = (
            p.cbr_ri_shape if laser in p.cbr_laser_names else p.gbr_ri_shape)
        range_image = py_utils.HasShape(range_image, shape_to_check)

        ri_outputs['%s_%s' % (laser, returns)] = range_image

      # Extract beam inclinations and extrinsics
      outputs['%s_extrinsics' % laser] = tf.reshape(
          _Dense(features['%s_extrinsics' % laser]), [4, 4])

    # CBRs have uniform inclination
    for laser in p.cbr_laser_names:
      beam_inclination_min = tf.reshape(
          _Dense(features['%s_beam_inclination_min' % laser]), [])
      beam_inclination_max = tf.reshape(
          _Dense(features['%s_beam_inclination_max' % laser]), [])
      outputs['%s_beam_inclinations' % laser] = tf.stack(
          [beam_inclination_min, beam_inclination_max], axis=0)

    # GBRs have non-uniform inclinations defined by 64 floats.
    for laser in p.gbr_laser_names:
      outputs['%s_beam_inclinations' % laser] = tf.reshape(
          _Dense(features['%s_beam_inclinations' % laser]), [64])

    # Embed xyz onto each range image pixel.
    for laser in p.cbr_laser_names + p.gbr_laser_names:
      extrinsics = outputs['%s_extrinsics' % laser]
      inclinations = outputs['%s_beam_inclinations' % laser]
      if laser in p.cbr_laser_names:
        ri_shape = p.cbr_ri_shape

        # Convert from 2-tuple range inclination to the full range
        # via linear interpolation.
        #
        # CBR lasers currently are always uniform inclinations specified by a
        # length 2 vector.
        height = ri_shape[0]
        min_inclination = inclinations[0]
        max_inclination = inclinations[1]
        diff = max_inclination - min_inclination
        ratio = (.5 + tf.to_float(tf.range(0, height))) / tf.to_float(height)
        # interpolate from min to max inclination.
        inclinations = (ratio * diff) + min_inclination
      else:
        ri_shape = p.gbr_ri_shape

      pixel_pose = None
      if laser in p.gbr_laser_names:
        pixel_pose = tf.reshape(
            _Dense(features['%s_pose' % laser]),
            shape=p.gbr_ri_shape[0:2] + [4, 4])

      for returns in p.returns:
        range_image = ri_outputs['%s_%s' % (laser, returns)]
        range_image = tf.reshape(range_image, ri_shape)
        range_image_mask = range_image[..., 0] >= 0
        ri_xyz = tf.to_float(
            self._XYZFromRangeImage(range_image, range_image_mask, extrinsics,
                                    inclinations, pixel_pose, frame_pose))

        # Produce the NestedMap of xyz, features, mask.
        ri_result = py_utils.NestedMap({
            'xyz': ri_xyz,
            'features': range_image,
            'mask': tf.to_float(range_image_mask),
        })

        outputs['%s_%s' % (laser, returns)] = ri_result

    return py_utils.NestedMap(outputs)

  def _XYZFromRangeImage(self,
                         lidar_image,
                         lidar_image_mask,
                         extrinsics,
                         inclinations,
                         pixel_pose=None,
                         frame_pose=None):
    """Extract the cartesian coordinates from the range image.

    Args:
       lidar_image: [H, W, C] range image Tensor.
       lidar_image_mask: [H, W] boolean indicating which 2d coordinates in the
         lidar image are present.
       extrinsics: [4, 4] float matrix representing transformation matrix to
         world coordinates.
       inclinations: [V] beam inclinations vector.
       pixel_pose: [64, 2650, 4, 4] tensor representing per pixel pose of GBR.
       frame_pose: [4, 4] matrix representing vehicle to world transformation.

    Returns:
      [H, W, 3] range image cartesian coordinates.
    """
    height, width, channels = py_utils.GetShape(lidar_image, 3)

    conversion_dtype = tf.float32
    lidar_image = tf.cast(lidar_image, conversion_dtype)
    extrinsics = tf.cast(extrinsics, conversion_dtype)
    inclinations = tf.cast(inclinations, conversion_dtype)
    inclinations = tf.reverse(inclinations, axis=[-1])

    az_correction = py_utils.HasShape(
        tf.atan2(extrinsics[1, 0], extrinsics[0, 0]), [])
    ratios = (tf.cast(tf.range(width, 0, -1), dtype=conversion_dtype) -
              .5) / tf.cast(width, conversion_dtype)
    ratios = py_utils.HasShape(ratios, [width])

    azimuth = (ratios * 2. - 1.) * np.pi - az_correction[..., tf.newaxis]
    azimuth = py_utils.HasShape(azimuth, [width])

    lidar_image_mask = lidar_image_mask[..., tf.newaxis]
    lidar_image_mask = tf.tile(lidar_image_mask, [1, 1, channels])
    lidar_image = tf.where(lidar_image_mask, lidar_image,
                           tf.zeros_like(lidar_image))
    lidar_image_range = lidar_image[..., 0]

    azimuth = py_utils.HasShape(azimuth[tf.newaxis, ...], [1, width])
    inclinations = py_utils.HasShape(inclinations[..., tf.newaxis], [height, 1])

    cos_azimuth = tf.cos(azimuth)
    sin_azimuth = tf.sin(azimuth)
    cos_incl = tf.cos(inclinations)
    sin_incl = tf.sin(inclinations)

    x = cos_azimuth * cos_incl * lidar_image_range
    y = sin_azimuth * cos_incl * lidar_image_range
    z = sin_incl * lidar_image_range

    lidar_image_points = tf.stack([x, y, z], -1)
    lidar_image_points = py_utils.HasShape(lidar_image_points,
                                           [height, width, 3])
    rotation = extrinsics[0:3, 0:3]
    translation = extrinsics[0:3, 3][tf.newaxis, ...]

    # Transform the image points in cartesian coordinates to
    # the world coordinate system using the extrinsics matrix.
    #
    # We first flatten the points, apply rotation, then
    # reshape to restore the original input and then apply
    # translation.
    lidar_image_points = tf.matmul(
        tf.reshape(lidar_image_points, [-1, 3]), rotation, transpose_b=True)
    lidar_image_points = tf.reshape(lidar_image_points, [height, width, 3])
    lidar_image_points += translation

    lidar_image_points = py_utils.HasShape(lidar_image_points,
                                           [height, width, 3])
    # GBR uses per pixel pose.
    if pixel_pose is not None:
      pixel_pose_rotation = pixel_pose[..., 0:3, 0:3]
      pixel_pose_translation = pixel_pose[..., 0:3, 3]
      lidar_image_points = tf.einsum(
          'hwij,hwj->hwi', pixel_pose_rotation,
          lidar_image_points) + pixel_pose_translation
      if frame_pose is None:
        raise ValueError('frame_pose must be set when pixel_pose is set.')
      # To vehicle frame corresponding to the given frame_pose
      # [4, 4]
      world_to_vehicle = tf.matrix_inverse(frame_pose)
      world_to_vehicle_rotation = world_to_vehicle[0:3, 0:3]
      world_to_vehicle_translation = world_to_vehicle[0:3, 3]
      # [H, W, 3]
      lidar_image_points = tf.einsum(
          'ij,hwj->hwi', world_to_vehicle_rotation,
          lidar_image_points) + world_to_vehicle_translation[tf.newaxis,
                                                             tf.newaxis, :]

    return lidar_image_points

  def Shape(self):
    """Shape of BBoxes."""
    p = self.params
    shapes = {}
    for laser in p.cbr_laser_names:
      cbr_shape = p.cbr_ri_shape[:-1]
      for returns in p.returns:
        shape_dict = py_utils.NestedMap({
            'xyz': tf.TensorShape(cbr_shape + [3]),
            'features': tf.TensorShape(cbr_shape + [4]),
            'mask': tf.TensorShape(cbr_shape),
        })
        shapes['%s_%s' % (laser, returns)] = shape_dict

      shapes['%s_extrinsics' % laser] = tf.TensorShape([4, 4])
      shapes['%s_beam_inclinations' % laser] = tf.TensorShape([2])
    for laser in p.gbr_laser_names:
      gbr_shape = p.gbr_ri_shape[:-1]
      for returns in p.returns:
        shape_dict = py_utils.NestedMap({
            'xyz': tf.TensorShape(gbr_shape + [3]),
            'features': tf.TensorShape(gbr_shape + [4]),
            'mask': tf.TensorShape(gbr_shape),
        })
        shapes['%s_%s' % (laser, returns)] = shape_dict
      shapes['%s_extrinsics' % laser] = tf.TensorShape([4, 4])
      shapes['%s_beam_inclinations' % laser] = tf.TensorShape([64])

    return py_utils.NestedMap(shapes)

  def DType(self):
    """Dtypes of BBoxes."""
    p = self.params
    dtypes = {}
    for laser in p.cbr_laser_names + p.gbr_laser_names:
      for returns in p.returns:
        dtype_dict = py_utils.NestedMap({
            'xyz': tf.float32,
            'features': tf.float32,
            'mask': tf.float32,
        })
        dtypes['%s_%s' % (laser, returns)] = dtype_dict
      dtypes['%s_extrinsics' % laser] = tf.float32
      dtypes['%s_beam_inclinations' % laser] = tf.float32
    return py_utils.NestedMap(dtypes)


class FilterNLZPoints(input_preprocessors.Preprocessor):
  """Filters points that are in no-label-zones.

  This preprocessor expects features to contain the following keys:
  - lasers.points_xyz of shape [P, 3]
  - lasers.points_feature of shape [P, F]

  Modifies the following features:
  - lasers.points_xyz of shape [P2, 3]
  - lasers.points_feature of shape [P2, F]

  where P - P2 are the number of points dropped because the corresponding
    point was in a no-label-zone.
  """

  @classmethod
  def Params(cls):
    p = super(FilterNLZPoints, cls).Params()
    return p

  def TransformFeatures(self, features):
    # We assume that the lasers are not padded, and all points are real.
    if ('points_padding' in features.lasers and
        features.lasers.points_padding is not None):
      raise ValueError('FilterNLZPoints preprocessor does not support '
                       'padded lasers.')

    # The 3rd feature in the laser is 1.0 for points in a no-label-zone
    # and -1. for normal points.
    is_not_nlz = tf.not_equal(features.lasers.points_feature[:, 2], 1.0)
    features.lasers.points_xyz = tf.boolean_mask(features.lasers.points_xyz,
                                                 is_not_nlz)
    features.lasers.points_feature = tf.boolean_mask(
        features.lasers.points_feature, is_not_nlz)
    return features

  def TransformShapes(self, shapes):
    return shapes

  def TransformDTypes(self, dtypes):
    return dtypes


class WaymoSparseLaser(input_extractor.BaseExtractor):
  """Sparse laser input extractor for Waymo dataset."""

  @classmethod
  def Params(cls):
    """Defaults params."""
    extractors = hyperparams.Params()
    extractors.Define('lasers', WaymoLaserExtractor.Params(), '')
    extractors.Define('labels', WaymoLabelExtractor.Params(), '')
    extractors.Define('metadata', WaymoFrameMetadataExtractor.Params(), '')

    preprocessors = py_utils.NestedMap(
        count_points=input_preprocessors.CountNumberOfPointsInBoxes3D.Params(),
        viz_copy=input_preprocessors.CreateDecoderCopy.Params(),
        keep_xyz_range=input_preprocessors.DropLaserPointsOutOfRange.Params(),
        filter_nlz_points=FilterNLZPoints.Params(),
        select_centers=input_preprocessors.SparseCenterSelector.Params(),
        gather_features=input_preprocessors.SparseCellGatherFeatures.Params(),
        tile_anchors=input_preprocessors.TileAnchorBBoxes.Params(),
        assign_anchors=input_preprocessors.AnchorAssignment.Params(),
        pad_lasers=input_preprocessors.PadLaserFeatures.Params().Set(),
    )
    p = super(WaymoSparseLaser, cls).Params(extractors).Set(
        preprocessors=_NestedMapToParams(preprocessors),
        preprocessors_order=[
            'viz_copy',
            'keep_xyz_range',
            'filter_nlz_points',
            'count_points',
            'select_centers',
            'gather_features',
            'tile_anchors',
            'assign_anchors',
            'pad_lasers',
        ],
    )

    p.file_datasource = datasource.PrefixedDataSourceWrapper.Params()
    p.file_datasource.base_datasource = datasource.SimpleDataSource.Params()
    p.file_datasource.base_datasource.file_type = 'tfrecord'

    return p
