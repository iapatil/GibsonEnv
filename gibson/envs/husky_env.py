from gibson.envs.env_modalities import CameraRobotEnv, BaseRobotEnv
from gibson.envs.env_bases import *
from gibson.core.physics.robot_locomotors import Husky
from transforms3d import quaternions
import os
import numpy as np
import sys
import pybullet as p
from gibson.core.physics.scene_stadium import SinglePlayerStadiumScene
import pybullet_data
import cv2

CALC_OBSTACLE_PENALTY = 1

tracking_camera = {
    'yaw': 20,
    'z_offset': 0.5,
    'distance': 1,
    'pitch': -20
}

tracking_camera_top = {
    'yaw': 20,  # demo: living room, stairs
    'z_offset': 0.5,
    'distance': 1,
    'pitch': -20
}

class HuskyNavigateEnv(CameraRobotEnv):
    """Specfy navigation reward
    """
    def __init__(self, config, gpu_count=0):
        self.config = self.parse_config(config)
        assert(self.config["envname"] == self.__class__.__name__ or self.config["envname"] == "TestEnv")

        CameraRobotEnv.__init__(self, self.config, gpu_count, 
                                scene_type="building",
                                tracking_camera=tracking_camera)

        self.robot_introduce(Husky(self.config, env=self))
        self.scene_introduce()
        self.total_reward = 0
        self.total_frame = 0

    def add_text(self, img):
        font = cv2.FONT_HERSHEY_SIMPLEX
        x,y,z = self.robot.get_position()
        r,p,ya = self.robot.get_rpy()
        cv2.putText(img, 'x:{0:.4f} y:{1:.4f} z:{2:.4f}'.format(x,y,z), (10, 20), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, 'ro:{0:.4f} pth:{1:.4f} ya:{2:.4f}'.format(r,p,ya), (10, 40), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, 'potential:{0:.4f}'.format(self.potential), (10, 60), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, 'fps:{0:.4f}'.format(self.fps), (10, 80), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return img

    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        progress = float(self.potential - potential_old)

        feet_collision_cost = 0.0
        for i, f in enumerate(
                self.robot.feet):  # TODO: Maybe calculating feet contacts could be done within the robot code
            # print(f.contact_list())
            contact_ids = set((x[2], x[4]) for x in f.contact_list())
            # print("CONTACT OF '%d' WITH %d" % (contact_ids, ",".join(contact_names)) )
            if (self.ground_ids & contact_ids):
                # see Issue 63: https://github.com/openai/roboschool/issues/63
                # feet_collision_cost += self.foot_collision_cost
                self.robot.feet_contact[i] = 1.0
            else:
                self.robot.feet_contact[i] = 0.0
        # print(self.robot.feet_contact)

        electricity_cost  = self.electricity_cost  * float(np.abs(a*self.robot.joint_speeds).mean())  # let's assume we 
        electricity_cost  += self.stall_torque_cost * float(np.square(a).mean())


        steering_cost = self.robot.steering_cost(a)
        debugmode = 0
        if debugmode:
            print("steering cost", steering_cost)

        wall_contact = [pt for pt in self.robot.parts['base_link'].contact_list() if pt[6][2] > 0.15]
        wall_collision_cost = self.wall_collision_cost * len(wall_contact)

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)
        close_to_target = 0

        if self.robot.dist_to_target() < 2:
            close_to_target = 0.5

        angle_cost = self.robot.angle_cost()

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty = get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if debugmode:
            print("angle cost", angle_cost)

        debugmode = 0
        if (debugmode):
            print("Wall contact points", len(wall_contact))
            print("Collision cost", wall_collision_cost)
            print("electricity_cost", electricity_cost)
            print("close to target", close_to_target)
            #print("progress")
            #print(progress)
            #print("electricity_cost")
            #print(electricity_cost)
            #print("joints_at_limit_cost")
            #print(joints_at_limit_cost)
            #print("feet_collision_cost")
            #print(feet_collision_cost)

        rewards = [
            #alive,
            progress,
            #wall_collision_cost,
            close_to_target,
            steering_cost,
            angle_cost,
            obstacle_penalty
            #electricity_cost,
            #joints_at_limit_cost,
            #feet_collision_cost
        ]
        return rewards

    def _termination(self, debugmode=False):
        height = self.robot.get_position()[2]
        pitch = self.robot.get_rpy()[1]
        alive = float(self.robot.alive_bonus(height, pitch))
        
        alive = len(self.robot.parts['top_bumper_link'].contact_list()) == 0

        done = not alive or self.nframe > 250 or height < 0
        if done:
            print("Episode reset")
        return done

    def _flag_reposition(self):
        target_pos = self.robot.target_pos

        self.flag = None
        if self.gui and not self.config["display_ui"]:
            self.visual_flagId = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.5, 0.5, 0.5], rgbaColor=[1, 0, 0, 0.7])
            self.last_flagId = p.createMultiBody(baseVisualShapeIndex=self.visual_flagId, baseCollisionShapeIndex=-1, basePosition=[target_pos[0], target_pos[1], 0.5])

    def  _reset(self):
        self.total_frame = 0
        self.total_reward = 0
        obs = CameraRobotEnv._reset(self)
        self._flag_reposition()
        return obs


class HuskyGibsonFlagRunEnv(CameraRobotEnv):
    """Specfy flagrun reward
    """
    def __init__(self, config, gpu_count=0):
        self.config = self.parse_config(config)
        print(self.config["envname"])
        assert(self.config["envname"] == self.__class__.__name__ or self.config["envname"] == "TestEnv")
        CameraRobotEnv.__init__(self, self.config, gpu_count, 
                                scene_type="building",
                                tracking_camera=tracking_camera)

        self.robot_introduce(Husky(self.config, env=self))
        self.scene_introduce()

        self.total_reward = 0
        self.total_frame = 0
        self.flag_timeout = 1
        self.visualid = -1
        self.lastid = None
        self.gui = self.config["mode"] == "gui"
        
        if self.gui:
            self.visualid = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.2, 0.2], rgbaColor=[1, 0, 0, 0.7])
        self.colisionid = p.createCollisionShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.2, 0.2])

        self.lastid = None
        self.obstacle_dist = 100
        
    def _reset(self):
        obs = CameraRobotEnv._reset(self)
        return obs

    def _flag_reposition(self):
        #self.walk_target_x = self.np_random.uniform(low=-self.scene.stadium_halflen,
        #                                            high=+self.scene.stadium_halflen)
        #self.walk_target_y = self.np_random.uniform(low=-self.scene.stadium_halfwidth,
        #                                            high=+self.scene.stadium_halfwidth)
        force_x = self.np_random.uniform(-300,300)
        force_y = self.np_random.uniform(-300, 300)

        more_compact = 0.5  # set to 1.0 whole football field
        #self.walk_target_x *= more_compact
        #self.walk_target_y *= more_compact

        startx, starty, _ = self.robot.get_position()


        self.flag = None
        #self.flag = self.scene.cpp_world.debug_sphere(self.walk_target_x, self.walk_target_y, 0.2, 0.2, 0xFF8080)
        self.flag_timeout = 3000 / self.scene.frame_skip
        #print('targetxy', self.flagid, self.walk_target_x, self.walk_target_y, p.getBasePositionAndOrientation(self.flagid))
        #p.resetBasePositionAndOrientation(self.flagid, posObj = [self.walk_target_x, self.walk_target_y, 0.5], ornObj = [0,0,0,0])
        if self.lastid:
            p.removeBody(self.lastid)

        self.lastid = p.createMultiBody(baseMass = 1, baseVisualShapeIndex=self.visualid, baseCollisionShapeIndex=self.colisionid, basePosition=[startx, starty, 0.5])
        p.applyExternalForce(self.lastid, -1, [force_x,force_y,50], [0,0,0], p.LINK_FRAME)

        ball_xyz, _ = p.getBasePositionAndOrientation(self.lastid)

        self.robot.walk_target_x = ball_xyz[0]
        self.robot.walk_target_y = ball_xyz[1]

    def _rewards(self, action = None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        if self.flag_timeout > 225:
            progress = 0
        else:
            progress = float(self.potential - potential_old)

        if not a is None:
            electricity_cost = self.electricity_cost * float(np.abs(
                a * self.robot.joint_speeds).mean())  # let's assume we have DC motor with controller, and reverse current braking
            electricity_cost += self.stall_torque_cost * float(np.square(a).mean())
        else:
            electricity_cost = 0

        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        if alive == 0:
            alive_score = 0.1
        else:
            alive_score = -0.1

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)
        debugmode = 0
        if (debugmode):
            print("progress")
            print(progress)

        obstacle_penalty = 0

        #print("obs dist %.3f" %self.obstacle_dist)
        if self.obstacle_dist < 0.7:
            obstacle_penalty = self.obstacle_dist - 0.7

        rewards = [
            alive_score,
            progress,
            obstacle_penalty
        ]
        return rewards

    def _termination(self, debugmode=False):
        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        done = alive > 0 or self.nframe > 500
        if (debugmode):
            print("alive=")
            print(alive)
        print(len(self.robot.parts['top_bumper_link'].contact_list()), self.nframe, done)
        return done

    def _step(self, a):
        state, reward, done, meta = CameraRobotEnv._step(self, a)
        if self.flag_timeout <= 0 or (self.flag_timeout < 225 and self.robot.walk_target_dist < 0.8):
            self._flag_reposition()
        self.flag_timeout -= 1

        if "depth" in self.config["output"]:
            depth_obs = self.get_observations()["depth"]
            x_start = int(self.windowsz/2-16)
            x_end   = int(self.windowsz/2+16)
            y_start = int(self.windowsz/2-16)
            y_end   = int(self.windowsz/2+16)
            self.obstacle_dist = (np.mean(depth_obs[x_start:x_end, y_start:y_end, -1]))

        return state, reward, done, meta



def get_obstacle_penalty(robot, depth):
    screen_sz = robot.obs_dim[0]
    screen_delta = int(screen_sz / 8)
    screen_half  = int(screen_sz / 2)
    height_offset = int(screen_sz / 4)

    obstacle_dist = (np.mean(depth[screen_half  + height_offset - screen_delta : screen_half + height_offset + screen_delta, screen_half - screen_delta : screen_half + screen_delta, -1]))
    obstacle_penalty = 0
    OBSTACLE_LIMIT = 1.5
    if obstacle_dist < OBSTACLE_LIMIT:
       obstacle_penalty = (obstacle_dist - OBSTACLE_LIMIT)
    
    debugmode = 0
    if debugmode:
        #print("Obstacle screen", screen_sz, screen_delta)
        print("Obstacle distance", obstacle_dist)
        print("Obstacle penalty", obstacle_penalty)
    return obstacle_penalty