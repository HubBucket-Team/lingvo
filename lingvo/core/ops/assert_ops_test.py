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
"""Tests for assert_ops."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from lingvo.core import ops
from lingvo.core import test_utils
import tensorflow as tf


class AssertOpsTest(test_utils.TestCase):

  def testBasic(self):
    with self.session():
      ops.assert_shape_match([10, 20, 30, 40], [-1, -1, -1, -1]).run()
      ops.assert_shape_match([10, 20, 30, 40], [-1, 20, -1, -1]).run()
      ops.assert_shape_match([10, 20, 30, 40], [-1, 20, -1, 40]).run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError,
                                   "Yo mismatch"):
        ops.assert_shape_match([10, 20, 30, 40], [10, 20, 40], "Yo").run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError,
                                   "Foo mismatch"):
        ops.assert_shape_match([10, 20, 30, 40], [8, 20, -1, 40], "Foo").run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError, "mismatch"):
        ops.assert_shape_match([10, 20, 30, 40], [10, 20, 30, 44]).run()

  def testSameBatchSize(self):

    def t(*args):
      return tf.zeros(shape=args, dtype=tf.float32)

    with self.session():
      ops.assert_same_dim0([t(2, 3, 4), t(2, 3, 4)]).run()
      ops.assert_same_dim0([t(2, 3), t(2, 8)]).run()
      ops.assert_same_dim0([t(2, 3)]).run()
      ops.assert_same_dim0([t(2, 3)] * 100).run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError, "a scalar"):
        ops.assert_same_dim0([t(), t(2, 3)]).run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError, "a scalar"):
        ops.assert_same_dim0([t(2, 3), t()]).run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError,
                                   "different dim0"):
        ops.assert_same_dim0([t(2, 3), t(3, 2)]).run()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError,
                                   "different dim0"):
        ops.assert_same_dim0([t(2, 3)] * 10 + [t(3, 2)]).run()


if __name__ == "__main__":
  tf.test.main()
