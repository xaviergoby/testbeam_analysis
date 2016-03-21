''' Script to check the correctness of the geometry utils functions (rotation, translation matrices)
'''
import os
import numpy as np

import unittest

from testbeam_analysis import geometry_utils

tests_data_folder = r'tests/test_track_analysis/'


class TestTrackAnalysis(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):  # remove created files
        pass

    def test_transformations(self):  # Transforms from global to local system and back and checks for equality
        position = np.array([0, 0, 0])  # Position in global system to transfrom

        for position in (np.array([-1, -2, -3]), np.array([0, 1, 0]), np.array([3, 2, 1])):
            for x in range(-3, 4, 3):  # Loop over x translation values
                for y in range(-3, 4, 3):  # Loop over y translation values
                    for z in range(-3, 4, 3):  # Loop over z translation values
                        for alpha in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop x rotation values
                            for beta in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop y rotation values
                                for gamma in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop z rotation values
                                    position_g = np.array([position[0], position[1], position[2], 1])  # Extend global position dimension
                                    transformation_matrix_to_local = geometry_utils.global_to_local_transformation_matrix(x, y, z, alpha, beta, gamma)
                                    transformation_matrix_to_global = geometry_utils.local_to_global_transformation_matrix(x, y, z, alpha, beta, gamma)
                                    position_l = np.dot(transformation_matrix_to_local, position_g)  # Transform to local coordinate system
                                    position_g_result = np.dot(transformation_matrix_to_global, position_l)  # Transform back to global coordinate system
                                    self.assertTrue(np.allclose(position, np.array(position_g_result[:-1])))  # Finite precision needs equality check with finite precision

    def test_rotation_matrices(self):
        # Check that the rotation matrices in x, y, z have the features of a rotation matrix (det = 1, inverse = transposed matrix)
        for alpha in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop x rotation values
            rotation_matrix_x = geometry_utils.rotation_matrix_x(alpha)
            self.assertAlmostEqual(np.linalg.det(rotation_matrix_x), 1)
            self.assertTrue(np.allclose(rotation_matrix_x.T, np.linalg.inv(rotation_matrix_x)))

        for beta in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop y rotation values
            rotation_matrix_y = geometry_utils.rotation_matrix_y(beta)
            self.assertAlmostEqual(np.linalg.det(rotation_matrix_y), 1)
            self.assertTrue(np.allclose(rotation_matrix_y.T, np.linalg.inv(rotation_matrix_y)))

        for gamma in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop z rotation values
            rotation_matrix_z = geometry_utils.rotation_matrix_z(gamma)
            self.assertAlmostEqual(np.linalg.det(rotation_matrix_z), 1)
            self.assertTrue(np.allclose(rotation_matrix_z.T, np.linalg.inv(rotation_matrix_z)))

        # Check that the rotation matrix build from x, y, z rotation matrices has the features of rotation matrix (det = 1, inverse = transposed matrix)
        for alpha in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop x rotation values
            for beta in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop y rotation values
                for gamma in [0, np.pi / 4., np.pi / 3., np.pi / 2., 3 * np.pi / 4., np.pi, 4. * np.pi / 3.]:  # Loop z rotation values
                    rotation_matrix = geometry_utils.rotation_matrix(alpha, beta, gamma)
                    self.assertAlmostEqual(np.linalg.det(rotation_matrix), 1)
                    self.assertTrue(np.allclose(rotation_matrix.T, np.linalg.inv(rotation_matrix)))

    def test_apply_transformation_matrix(self):
        # Test 1: Transformation matrix that is a translation by (1, 2, 3) without rotation
        transformation_matrix = geometry_utils.global_to_local_transformation_matrix(x=1,
                                                                                     y=2,
                                                                                     z=3,
                                                                                     alpha=0.0,
                                                                                     beta=0.0,
                                                                                     gamma=0.0)
        x_new, y_new, z_new = geometry_utils.apply_transformation_matrix(x=np.arange(10),
                                                                         y=np.arange(10),
                                                                         z=np.arange(10),
                                                                         transformation_matrix=transformation_matrix)

        self.assertTrue(np.all(x_new == np.arange(10) - 1))
        self.assertTrue(np.all(y_new == np.arange(10) - 2))
        self.assertTrue(np.all(z_new == np.arange(10) - 3))

if __name__ == '__main__':
    tests_data_folder = r'test_track_analysis/'
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTrackAnalysis)
    unittest.TextTestRunner(verbosity=2).run(suite)