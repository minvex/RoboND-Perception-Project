#!/usr/bin/env python

# Import modules
import numpy as np
import sklearn
from sklearn.preprocessing import LabelEncoder
import pickle
from sensor_stick.srv import GetNormals
from sensor_stick.features import compute_color_histograms
from sensor_stick.features import compute_normal_histograms
from visualization_msgs.msg import Marker
from sensor_stick.marker_tools import *
from sensor_stick.msg import DetectedObjectsArray
from sensor_stick.msg import DetectedObject
from sensor_stick.pcl_helper import *

import rospy
import tf
from geometry_msgs.msg import Point
from geometry_msgs.msg import Pose
from std_msgs.msg import Float64
from std_msgs.msg import Int32
from std_msgs.msg import String
from pr2_robot.srv import *
from rospy_message_converter import message_converter
import yaml

max_success_count = 0

# Helper function to get surface normals
def get_normals(cloud):
    get_normals_prox = rospy.ServiceProxy('/feature_extractor/get_normals', GetNormals)
    return get_normals_prox(cloud).cluster

# Helper function to create a yaml friendly dictionary from ROS messages
def make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose):
    yaml_dict = {}
    yaml_dict["test_scene_num"] = test_scene_num.data
    yaml_dict["arm_name"]  = arm_name.data
    yaml_dict["object_name"] = object_name.data
    yaml_dict["pick_pose"] = message_converter.convert_ros_message_to_dictionary(pick_pose)
    yaml_dict["place_pose"] = message_converter.convert_ros_message_to_dictionary(place_pose)
    return yaml_dict

# Helper function to output to yaml file
def send_to_yaml(yaml_filename, dict_list):
    data_dict = {"object_list": dict_list}
    with open(yaml_filename, 'w') as outfile:
        yaml.dump(data_dict, outfile, default_flow_style=False)

# Callback function for your Point Cloud Subscriber
def pcl_callback(pcl_msg):

    # Classify the clusters!
    detected_objects_labels = []
    detected_objects = []
    # detected_objects label list
    labels = []
    # to be list of tuples (x, y, z)
    centroids = [] 

# Exercise-2 TODOs:

    # TODO: Convert ROS msg to PCL data
    cloud = ros_to_pcl(pcl_msg)

    # TODO: Voxel Grid Downsampling
    vox = cloud.make_voxel_grid_filter()
    LEAF_SIZE = 0.01
    vox.set_leaf_size(LEAF_SIZE, LEAF_SIZE, LEAF_SIZE)
    cloud_filtered = vox.filter()
    
    # TODO: Statistical Outlier Filtering
    cloud_filtered = cloud_filtered.make_statistical_outlier_filter()

    # Set the number of neighboring points to analyze for any given point
    cloud_filtered.set_mean_k(16)

    # Set threshold scale factor
    x = 0.01   # need to test different values

    # Any point with a mean distance larger than global (mean distance+x*std_dev) will be considered outlier
    cloud_filtered.set_std_dev_mul_thresh(x)

    # Finally call the filter function for magic
    cloud_filtered = cloud_filtered.filter()

    # TODO: PassThrough Filter
    passthrough = cloud_filtered.make_passthrough_filter()
    filter_axis = 'z'
    passthrough.set_filter_field_name (filter_axis)
    axis_min = 0.6
    axis_max = 2.0
    passthrough.set_filter_limits (axis_min, axis_max)
    cloud_filtered = passthrough.filter()

    passthrough = cloud_filtered.make_passthrough_filter()
    filter_axis = 'y'
    passthrough.set_filter_field_name (filter_axis)
    axis_min = -0.4
    axis_max = 0.4
    passthrough.set_filter_limits (axis_min, axis_max)
    cloud_filtered = passthrough.filter()

    # TODO: RANSAC Plane Segmentation
    seg = cloud_filtered.make_segmenter()
    seg.set_model_type(pcl.SACMODEL_PLANE)
    seg.set_method_type(pcl.SAC_RANSAC)
    max_distance = 0.01
    seg.set_distance_threshold(max_distance)
    inliers, coefficients = seg.segment()

    # TODO: Extract inliers and outliers
    cloud_table = cloud_filtered.extract(inliers, negative=False)   # table
    cloud_objects = cloud_filtered.extract(inliers, negative=True)   # object

    # TODO: Euclidean Clustering
    white_cloud = XYZRGB_to_XYZ(cloud_objects)
    tree = white_cloud.make_kdtree()
    # Create a cluster extraction object
    ec = white_cloud.make_EuclideanClusterExtraction()
    # Set tolerances for distance threshold 
    # as well as minimum and maximum cluster size (in points)
    # NOTE: These are poor choices of clustering parameters
    # Your task is to experiment and find values that work for segmenting objects.
    ec.set_ClusterTolerance(0.03)
    ec.set_MinClusterSize(20)
    ec.set_MaxClusterSize(2000)
    # Search the k-d tree for clusters
    ec.set_SearchMethod(tree)
    # Extract indices for each of the discovered clusters
    cluster_indices = ec.Extract()

    # TODO: Create Cluster-Mask Point Cloud to visualize each cluster separately
    #Assign a color corresponding to each segmented object in scene
    cluster_color = get_color_list(len(cluster_indices))

    color_cluster_point_list = []

    for j, indices in enumerate(cluster_indices):
        for i, indice in enumerate(indices):
            color_cluster_point_list.append([white_cloud[indice][0],
                                        white_cloud[indice][1],
                                        white_cloud[indice][2],
                                         rgb_to_float(cluster_color[j])])

    #Create new cloud containing all clusters, each with unique color
    cluster_cloud = pcl.PointCloud_PointXYZRGB()
    cluster_cloud.from_list(color_cluster_point_list)

    # TODO: Convert PCL data to ROS messages
    ros_cloud_objects =  pcl_to_ros(cloud_objects)
    ros_cloud_table = pcl_to_ros(cloud_table)
    ros_cluster_cloud = pcl_to_ros(cluster_cloud)

    # TODO: Publish ROS messages
    pcl_objects_pub.publish(ros_cloud_objects)
    pcl_table_pub.publish(ros_cloud_table)
    pcl_cluster_pub.publish(ros_cluster_cloud)

# Exercise-3 TODOs:

    # Classify the clusters! (loop through each detected cluster one at a time)
    for index, pts_list in enumerate(cluster_indices):

        # Grab the points for the cluster
        pcl_cluster = cloud_objects.extract(pts_list)

        # TODO: convert the cluster from pcl to ROS using helper function
        ros_cluster = pcl_to_ros(pcl_cluster)

        # Compute the associated feature vector
        # Extract histogram features
        # TODO: complete this step just as is covered in capture_features.py
        chists = compute_color_histograms(ros_cluster, using_hsv=True)
        normals = get_normals(ros_cluster)
        nhists = compute_normal_histograms(normals)
        feature = np.concatenate((chists, nhists))

        # Make the prediction
        # and add it to detected_objects_labels list
        prediction = clf.predict(scaler.transform(feature.reshape(1,-1)))
        label = encoder.inverse_transform(prediction)[0]
        detected_objects_labels.append(label)
        #print("label = %s" % label)

        # Publish a label into RViz
        label_pos = list(white_cloud[pts_list[0]])
        label_pos[2] += .4
        #print("label position = %s", label_pos)
        object_markers_pub.publish(make_label(label,label_pos, index))

        # Add the detected object to the list of detected objects.
        do = DetectedObject()
        do.label = label
        do.cloud = ros_cluster
        detected_objects.append(do)

    rospy.loginfo('Detected {} objects: {}'.format(len(detected_objects_labels), detected_objects_labels))

    # Publish the list of detected objects
    # This is the output you'll need to complete the upcoming project!
    detected_objects_pub.publish(detected_objects)

    for obj in detected_objects:
        labels.append(obj.label)
        points_arr = ros_to_pcl(obj.cloud).to_array()
        centroids.append(np.mean(points_arr, axis=0)[:3])

    detected_objects_list = dict(zip(labels, centroids))

    # Suggested location for where to invoke your pr2_mover() function within pcl_callback()
    # Could add some logic to determine whether or not your object detections are robust
    # before calling pr2_mover()
    try:
        pr2_mover(detected_objects_list)
    except rospy.ROSInterruptException:
        pass
    return

# define dropbox data class
class dropbox_data(object):
    def __init__(self, position, arm):
        self.pos = position
        self.arm = arm
    def show():
        print("arm = %s, pos = %f",(self.arm, self.pos))

# function to load parameters and request PickPlace service
def pr2_mover(object_list):
    print('start mover function')
    global  max_success_count
    # TODO: Initialize variables
    test_scene_num = Int32()
    arm_name = String() 
    object_name = String()
    object_group = String()
    pick_pose = Pose()
    place_pose = Pose()
    dropbox_dict = {}
    dict_list = []
    pick_pose_point = Point()
    place_pose_point = Point()
    success_count = 0

    # TODO: Get/Read parameters
    object_list_param = rospy.get_param('/object_list')
    # Get scene number from launch file
    test_scene_num.data = rospy.get_param('/test_scene_num')
    # print("test_scene_num = %d"% test_scene_num.data)
    # Get dropbox parameters
    dropbox_param = rospy.get_param('/dropbox')

    # TODO: Parse parameters into individual variables
    for dropbox in dropbox_param:
        dropboxdata = dropbox_data(dropbox['position'], dropbox['name'])
        dropbox_dict[dropbox['group']] = dropboxdata


    # TODO: Rotate PR2 in place to capture side tables for the collision map

    # TODO: Loop through the pick list
    for obj in object_list_param:

        group = obj['group']
        name = obj['name']
        pos = object_list.get(name)

        # TODO: Get the PointCloud for a given object and obtain it's centroid
        if pos is not None:

            object_name.data = name
            pick_pose_point.x = np.asscalar(pos[0])
            pick_pose_point.y = np.asscalar(pos[1])
            pick_pose_point.z = np.asscalar(pos[2])
            pick_pose.position = pick_pose_point

        # TODO: Create 'place_pose' for the object
            dropboxdata = dropbox_dict[group]
            place_pose_point.x = dropboxdata.pos[0]
            place_pose_point.y = dropboxdata.pos[1]
            place_pose_point.z = dropboxdata.pos[2]
            place_pose.position = place_pose_point

        # TODO: Assign the arm to be used for pick_place
            arm_name.data = dropboxdata.arm
	    print("Scene %d, picking up found %s object, using %s arm, and placing it in the %s bin." % 
                  (test_scene_num.data, object_name.data, arm_name.data, group))


        # TODO: Create a list of dictionaries (made with make_yaml_dict()) for later output to yaml format
            yaml_dict = make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose)
            dict_list.append(yaml_dict)
            success_count = success_count + 1
        else:
            print("Label: %s not found" % name)

        # Wait for 'pick_place_routine' service to come up
 #       rospy.wait_for_service('pick_place_routine')

#        try:
#            pick_place_routine = rospy.ServiceProxy('pick_place_routine', PickPlace)

            # TODO: Insert your message variables to be sent as a service request
            #resp = pick_place_routine(TEST_SCENE_NUM, OBJECT_NAME, WHICH_ARM, PICK_POSE, PLACE_POSE)
#            resp = pick_place_routine(test_scene_num, object_name, arm_name, pick_pose, place_pose)

#            print("Response: ",resp.success)

#        except rospy.ServiceException, e:
#            print("Service call failed: %s"%e)

    # TODO: Output your request parameters into output yaml file
    if max_success_count < success_count:
       yaml_filename = "./output/output_" + str(test_scene_num.data) + ".yaml"
       print("output file name = %s" % yaml_filename)
       send_to_yaml(yaml_filename, dict_list)
       max_success_count = success_count
    print("Success picking up object number = %d" % success_count)
    return


if __name__ == '__main__':

    # TODO: ROS node initialization
    rospy.init_node('pr2', anonymous=True)

    # TODO: Create Subscribers
    #pcl_sub = rospy.Subscriber("/sensor_stick/point_cloud", pc2.PointCloud2, pcl_callback, queue_size=1)
    pcl_sub = rospy.Subscriber("/pr2/world/points", pc2.PointCloud2, pcl_callback, queue_size=1)


    # TODO: Create Publishers
    pcl_objects_pub = rospy.Publisher("/pcl_objects", PointCloud2, queue_size=1)
    pcl_table_pub = rospy.Publisher("/pcl_table", PointCloud2, queue_size=1)
    pcl_cluster_pub = rospy.Publisher("/pcl_cluster", PointCloud2, queue_size=1)
    object_markers_pub = rospy.Publisher("/object_markers", Marker, queue_size=1)
    detected_objects_pub = rospy.Publisher("/detected_objects", DetectedObjectsArray, queue_size=1)


    # TODO: Load Model From disk
    model = pickle.load(open('./training/model.sav', 'rb'))
    clf = model['classifier']
    encoder = LabelEncoder()
    encoder.classes_ = model['classes']
    scaler = model['scaler']

    # Initialize color_list
    get_color_list.color_list = []

    # TODO: Spin while node is not shutdown
    while not rospy.is_shutdown():
        rospy.spin()
