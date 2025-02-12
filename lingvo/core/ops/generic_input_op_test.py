# Lint as: python2, python3
# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
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
"""Tests for generic_input_op."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import os
import pickle
from absl.testing import parameterized
from lingvo import compat as tf
from lingvo.core import generic_input
from lingvo.core import py_utils
from lingvo.core import test_utils
import numpy as np
from six.moves import range


class GenericInputOpTest(test_utils.TestCase, parameterized.TestCase):

  def get_test_input(self, path, **kwargs):
    return generic_input.GenericInput(
        file_pattern='tfrecord:' + path,
        file_random_seed=0,
        file_buffer_size=32,
        file_parallelism=4,
        bucket_batch_limit=[8],
        **kwargs)

  @parameterized.named_parameters(('OutputList', False),
                                  ('OutputNestedMap', True))
  def testBasic(self, use_nested_map):
    input_batch = self._RunBasicGraph(use_nested_map=use_nested_map)
    with self.session() as sess:
      record_seen = set()
      for i in range(100):
        ans_input_batch = sess.run(input_batch)
        for s in ans_input_batch.record:
          record_seen.add(s)
        self.assertEqual(ans_input_batch.source_id.shape, (8,))
        self.assertEqual(ans_input_batch.record.shape, (8,))
        self.assertEqual(ans_input_batch.num.shape, (8, 2))
        ans_vals = ans_input_batch.num
        self.assertAllEqual(np.square(ans_vals[:, 0]), ans_vals[:, 1])
      for i in range(100):
        self.assertIn(('%08d' % i).encode('utf-8'), record_seen)

  def _RunBasicGraph(self, use_nested_map, bucket_fn=lambda x: 1):
    # Generate a test file w/ 100 records.
    tmp = os.path.join(tf.test.get_temp_dir(), 'basic')
    with tf.python_io.TFRecordWriter(tmp) as w:
      for i in range(100):
        w.write(('%08d' % i).encode('utf-8'))

    # A simple string parsing routine. Just convert a string to a
    # number.
    def str_to_num(s):
      return np.array(float(s), dtype=np.float32)

    # A record processor written in TF graph.
    def _process(source_id, record):
      num, = tf.py_func(str_to_num, [record], [tf.float32])
      num = tf.stack([num, tf.square(num)])
      if use_nested_map:
        return py_utils.NestedMap(
            source_id=source_id, record=record, num=num), bucket_fn(num)
      else:
        return [source_id, record, num], bucket_fn(num)

    # Samples random records from the data files and processes them
    # to generate batches.
    inputs, _ = self.get_test_input(
        tmp, bucket_upper_bound=[1], processor=_process)
    if use_nested_map:
      return inputs
    else:
      src_ids, strs, vals = inputs
      return py_utils.NestedMap(source_id=src_ids, record=strs, num=vals)

  def testPadding(self):
    # Generate a test file w/ 50 records of different lengths.
    tmp = os.path.join(tf.test.get_temp_dir(), 'basic')
    with tf.python_io.TFRecordWriter(tmp) as w:
      for n in range(1, 50):
        w.write(pickle.dumps(np.full([n, 3, 3], n, np.int32)))

    g = tf.Graph()
    with g.as_default():
      # A record processor written in TF graph.
      def _process(record):
        num = tf.py_func(pickle.loads, [record], tf.int32)
        bucket_key = tf.shape(num)[0]
        return [num, tf.transpose(num, [1, 0, 2])], bucket_key

      # Samples random records from the data files and processes them
      # to generate batches.
      (vals_t, transposed_vals_t), _ = self.get_test_input(
          tmp,
          bucket_upper_bound=[10],
          processor=_process,
          dynamic_padding_dimensions=[0, 1],
          dynamic_padding_constants=[0] * 2)

    with self.session(graph=g) as sess:
      for _ in range(10):
        vals, transposed_vals = sess.run([vals_t, transposed_vals_t])
        print(vals, np.transpose(transposed_vals, [0, 2, 1, 3]))
        self.assertEqual(vals.shape[0], 8)
        self.assertEqual(vals.shape[2], 3)
        self.assertEqual(vals.shape[3], 3)
        largest = np.amax(vals)
        self.assertLessEqual(largest, 10)
        self.assertEqual(vals.shape[1], largest)
        for j in range(8):
          n = vals[j, 0, 0, 0]
          self.assertTrue(np.all(vals[j, :n] == n))
          self.assertTrue(np.all(vals[j, n:] == 0))
        self.assertAllEqual(vals, np.transpose(transposed_vals, [0, 2, 1, 3]))

  def testDropRecordIfNegativeBucketKey(self):

    def bucket_fn(num):
      # Drops record if num[0] is odd.
      return tf.cond(
          tf.equal(tf.mod(num[0], 2), 0), lambda: 1,
          lambda: -tf.to_int32(num[0]))

    input_batch = self._RunBasicGraph(use_nested_map=False, bucket_fn=bucket_fn)

    with self.session() as sess:
      record_seen = set()
      for i in range(100):
        ans_input_batch = sess.run(input_batch)
        for s in ans_input_batch.record:
          record_seen.add(s)
      for i in range(100):
        if i % 2 == 0:
          self.assertIn(('%08d' % i).encode('utf-8'), record_seen)
        else:
          self.assertNotIn(('%08d' % i).encode('utf-8'), record_seen)

  def testWithinBatchMixing(self):
    # Generate couple files.
    def generate_test_data(tag, cnt):
      tmp = os.path.join(tf.test.get_temp_dir(), tag)
      with tf.python_io.TFRecordWriter(tmp) as w:
        for i in range(cnt):
          w.write(('%s:%08d' % (tag, i)).encode('utf-8'))
      return tmp

    path1 = generate_test_data('input1', 100)
    path2 = generate_test_data('input2', 200)
    path3 = generate_test_data('input3', 10)

    g = tf.Graph()
    with g.as_default():
      # A record processor written in TF graph.
      def _process(source_id, record):
        return py_utils.NestedMap(source_id=source_id, record=record), 1

      # Samples random records from the data files and processes them
      # to generate batches.
      input_batch, buckets = generic_input.GenericInput(
          file_pattern=','.join(
              ['tfrecord:' + path1, 'tfrecord:' + path2, 'tfrecord:' + path3]),
          input_source_weights=[0.2, 0.3, 0.5],
          file_random_seed=0,
          file_buffer_size=32,
          file_parallelism=4,
          bucket_batch_limit=[8],
          bucket_upper_bound=[1],
          processor=_process)

    with self.session(graph=g) as sess:
      source_id_count = collections.defaultdict(int)
      tags_count = collections.defaultdict(int)
      total_count = 10000
      for _ in range(total_count):
        ans_input_batch, ans_buckets = sess.run([input_batch, buckets])
        for s in ans_input_batch.source_id:
          source_id_count[s] += 1
        for s in ans_input_batch.record:
          tags_count[s.split(b':')[0]] += 1
        self.assertEqual(ans_input_batch.source_id.shape, (8,))
        self.assertEqual(ans_input_batch.record.shape, (8,))
        self.assertAllEqual(ans_buckets, [1] * 8)
      self.assertEqual(sum(source_id_count.values()), total_count * 8)
      self.assertEqual(sum(tags_count.values()), total_count * 8)
      num_records = 8. * total_count
      self.assertAlmostEqual(
          tags_count[b'input1'] / num_records, 0.2, delta=0.01)
      self.assertAlmostEqual(
          tags_count[b'input2'] / num_records, 0.3, delta=0.01)
      self.assertAlmostEqual(
          tags_count[b'input3'] / num_records, 0.5, delta=0.01)
      self.assertAlmostEqual(source_id_count[0] / num_records, 0.2, delta=0.01)
      self.assertAlmostEqual(source_id_count[1] / num_records, 0.3, delta=0.01)
      self.assertAlmostEqual(source_id_count[2] / num_records, 0.5, delta=0.01)


if __name__ == '__main__':
  tf.test.main()
