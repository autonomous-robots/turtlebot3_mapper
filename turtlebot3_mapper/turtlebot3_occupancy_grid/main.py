import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor

from turtlebot3_mapper.turtlebot3_occupancy_grid.turtlebot3_occupancy_grid \
    import Turtlebot3OccupancyGrid


def main(*args, **kwargs):
    rclpy.init(*args, **kwargs)
    node = Turtlebot3OccupancyGrid(node_name="turtlebot3_occupancy_grid")
    executor = MultiThreadedExecutor()
    try:
        rclpy.spin(node=node, executor=executor)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()