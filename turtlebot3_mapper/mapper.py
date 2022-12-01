import rclpy
import threading
import numpy as np

from rclpy.node import Node
from rclpy.executors import ExternalShutdownException

from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry, OccupancyGrid, MapMetaData

from tf2_ros.buffer import Buffer
from tf2_ros import LookupException, TransformException 
from tf2_ros.transform_listener import TransformListener

from turtlebot3_mapper.utils import euler_from_quaternion

class TurtlebotMapper(Node):

    def __init__(self,
                 node_name: str = 'turtlebot_mapper',
                 width: int = 8,
                 height: int = 8,
                 resolution: float = 0.03):

        super(TurtlebotMapper, self).__init__(node_name=node_name)
        self.width = width
        self.height = height
        self.resolution = resolution

        N = int(1/resolution)
        shape = (width*N, height*N)
        self.grid = -1*np.ones(shape, dtype=np.int8)

        self._scan_subscriber = self.create_subscription(
            msg_type=LaserScan,
            topic="/scan",
            callback=self._scan_callback,
            qos_profile=10,
        )
        self._map_publisher = self.create_publisher(
            msg_type=OccupancyGrid,
            topic="/custom_map",
            qos_profile=10,
        )
        self._update_timer = self.create_timer(
            timer_period_sec=0.1,
            callback=self._update_callback,
        )
        self._scan_init = False
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._update = threading.Lock()
        self.get_logger().info(f"Init {node_name}")

    def _scan_callback(self, message: LaserScan):
        self._scan_init = True
        if not self._update.locked():
            self._scan = message

    def _update_callback(self):
        if self._scan_init:
            if self._update.locked():
                return
            else:
                self._update.acquire()
                self.get_logger().info("updating...")
                self.update(
                    message_laser=self._scan,
                )
                self._map_publisher.publish(self.occupancy_grid)
                self._update.release()

        
    def update(self, message_laser: LaserScan):
        try:
            tf = self._tf_buffer.lookup_transform('odom',
                                                 message_laser.header.frame_id,
                                                 message_laser.header.stamp)
            q = tf.transform.rotation
        except (TransformException, LookupException):
            self.get_logger().info("could not get tf")
            return
        _, _, theta = euler_from_quaternion(quaternion=q)
        if theta < 0.0:
            theta += 2*np.pi
        distances = self.scan_to_distances(message=message_laser)
        xy = self.distances_to_xy(
            distances=distances,
            x=tf.transform.translation.x,
            y=tf.transform.translation.y,
            theta=theta,
        )
        xy[:,0] = (xy[:,0] + self.width//2)/self.resolution
        xy[:,1] = (xy[:,1] + self.height//2)/self.resolution
        xy = xy.astype(int)
        xo = int((tf.transform.translation.x + self.width//2)/self.resolution)
        yo = int((tf.transform.translation.y + self.height//2)/self.resolution)

        for i in range(xy.shape[0]):
            points = self.bresenham(
                x1=xo,
                y1=yo,
                x2=xy[i,0],
                y2=xy[i,1],
            )

            for j in range(points.shape[0] - 1):
                x = points[j,0]
                y = points[j,1]
                if self.grid[y, x] == 100:
                    continue
                self.grid[y, x] = 0

            x = xy[i,0]
            y = xy[i,1]
            if distances[i,0] < message_laser.range_max:
                self.grid[y, x] = 100
                self.grid[y+1, x] = 100
                self.grid[y-1, x] = 100
                self.grid[y, x-1] = 100
                self.grid[y, x+1] = 100
            else:
                if self.grid[y, x] != 100:
                    self.grid[y, x] = 0
    
    @property
    def occupancy_grid(self):
        return self.numpy_to_occupancy_grid(arr=self.grid)

    @staticmethod
    def scan_to_distances(message: LaserScan) -> np.ndarray:
        # N samples
        N = len(message.ranges)
        array = np.zeros((N, 2))
        for i in range(len(message.ranges)):
            angle = i * message.angle_increment
            if message.ranges[i] > message.range_max:
                distance = message.range_max
            elif message.ranges[i] < message.range_min:
                distance = message.range_min
            else:
                distance = message.ranges[i]
            # distances in [m]
            array[i,0] = distance
            # angles in [radians]
            array[i,1] = angle
        return array

    @staticmethod
    def distances_to_xy(distances: np.ndarray, x: float, y: float, theta: float) -> np.ndarray:
        # N samples
        N = distances.shape[0]
        array = np.zeros((N,2))
        array[:,0] = x + distances[:,0]*np.cos(distances[:,1] + theta)
        array[:,1] = y + distances[:,0]*np.sin(distances[:,1] + theta)
        return array


    @staticmethod
    def numpy_to_occupancy_grid(arr, info=None):
        """
        Source: http://docs.ros.org/en/jade/api/ros_numpy/html/occupancy__grid_8py_source.html
        """
        if not len(arr.shape) == 2:
            raise TypeError('Array must be 2D')
        if not arr.dtype == np.int8:
            raise TypeError('Array must be of int8s')
        grid = OccupancyGrid()
        if isinstance(arr, np.ma.MaskedArray):
            # We assume that the masked value are already -1, for speed
            arr = arr.data
        grid.data = arr.ravel().tolist()
        grid.info = info or MapMetaData()
        grid.info.height = arr.shape[0]
        grid.info.width = arr.shape[1]
        return grid

    @staticmethod
    def bresenham(x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        """
        Implementation of Bresenham's line drawing algorithm.
        >>> points1 = bresenham((4, 4), (6, 10))
        >>> print(points1)
        np.array([[4,4], [4,5], [5,6], [5,7], [5,8], [6,9], [6,10]])

        Source: https://github.com/AtsushiSakai/PythonRobotics
        """
        # setup initial conditions
        dx = x2 - x1
        dy = y2 - y1
        # determine how steep the line is
        is_steep = abs(dy) > abs(dx)
        # rotate line
        if is_steep:
            x1, y1 = y1, x1
            x2, y2 = y2, x2
        # swap start and end points if necessary and store swap state
        swapped = False
        if x1 > x2:
            x1, x2 = x2, x1
            y1, y2 = y2, y1
            swapped = True
        # recalculate differentials
        dx = x2 - x1
        # recalculate differentials
        dy = y2 - y1
        # calculate error
        error = int(dx / 2.0)
        y_step = 1 if y1 < y2 else -1
        # iterate over bounding box generating points between start and end
        y = y1
        points = []
        for x in range(x1, x2 + 1):
            coord = [y, x] if is_steep else (x, y)
            points.append(coord)
            error -= abs(dy)
            if error < 0:
                y += y_step
                error += dx
        # reverse the list if the coordinates were swapped
        if swapped:
            points.reverse()
        return np.array(points)



def main(*args, **kwargs):
    rclpy.init(*args, **kwargs)
    node = TurtlebotMapper()
    try:
        rclpy.spin(node=node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
