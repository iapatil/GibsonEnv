"""This file implements the functionalities of a minitaur using pybullet.

"""
import copy
import math
import numpy as np
from gibson.core.physics import motor
from gibson.core.physics.robot_locomotors import WalkerBase
from gibson.core.physics.robot_bases import Joint, BodyPart
import os, sys
from gym import spaces
import pybullet as p
from transforms3d.euler import euler2quat

INIT_POSITION = [0, 0, 1.2]
INIT_ORIENTATION = [0, 0, 0, 1]
KNEE_CONSTRAINT_POINT_RIGHT = [0, 0.005, 0.2]
KNEE_CONSTRAINT_POINT_LEFT = [0, 0.01, 0.2]
OVERHEAT_SHUTDOWN_TORQUE = 2.45
OVERHEAT_SHUTDOWN_TIME = 1.0
LEG_POSITION = ["front_left", "back_left", "front_right", "back_right"]
MOTOR_NAMES = [
    "motor_front_leftL_joint", "motor_front_leftR_joint",
    "motor_back_leftL_joint", "motor_back_leftR_joint",
    "motor_front_rightL_joint", "motor_front_rightR_joint",
    "motor_back_rightL_joint", "motor_back_rightR_joint"
]
LEG_LINK_ID = [2, 3, 5, 6, 8, 9, 11, 12, 15, 16, 18, 19, 21, 22, 24, 25]
MOTOR_LINK_ID = [1, 4, 7, 10, 14, 17, 20, 23]
FOOT_LINK_ID = [3, 6, 9, 12, 16, 19, 22, 25]
BASE_LINK_ID = -1
OBSERVATION_DIM = 3 * len(MOTOR_NAMES) + 4   # VELOCITY, ANGLE, TORQUES


def quatWXYZ2quatXYZW(wxyz):
  return np.concatenate((wxyz[1:], wxyz[:1]))


class MinitaurBase(WalkerBase):
    """The minitaur class that simulates a quadruped robot from Ghost Robotics.
    """

    def __init__(self,
                 time_step=0.01,
                 self_collision_enabled=True,
                 motor_velocity_limit=np.inf,
                 pd_control_enabled=False,
                 accurate_motor_model_enabled=True,   ## (hzyjerry): affect speed?
                 motor_kp=1.20,
                 motor_kd=0.00,
                 torque_control_enabled=False,
                 motor_overheat_protection=False,
                 on_rack=False,
                 kd_for_pd_controllers=0.3, 

                 ## Below: for gibson
                 is_discrete=False, 
                 initial_pos=INIT_POSITION, 
                 initial_orn=INIT_ORIENTATION, 
                 target_pos=[1, 0, 0], 
                 resolution="NORMAL", 
                 mode="RGB"):

        """Constructs a minitaur and reset it to the initial states.

        Args:
        time_step: The time step of the simulation.
        self_collision_enabled: Whether to enable self collision.
        motor_velocity_limit: The upper limit of the motor velocity.
        pd_control_enabled: Whether to use PD control for the motors. If true, need smaller time step to stablize (1/500.0 timestep)
        accurate_motor_model_enabled: Whether to use the accurate DC motor model.
        motor_kp: proportional gain for the accurate motor model
        motor_kd: derivative gain for the acurate motor model
        torque_control_enabled: Whether to use the torque control, if set to
            False, pose control will be used.
        motor_overheat_protection: Whether to shutdown the motor that has exerted
            large torque (OVERHEAT_SHUTDOWN_TORQUE) for an extended amount of time
            (OVERHEAT_SHUTDOWN_TIME). See ApplyAction() in minitaur.py for more
            details.
        on_rack: Whether to place the minitaur on rack. This is only used to debug
            the walking gait. In this mode, the minitaur's base is hanged midair so
            that its walking gait is clearer to visualize.
        kd_for_pd_controllers: kd value for the pd controllers of the motors.
        """

        self.model_type = "URDF"
        self.is_discrete = is_discrete
        self.scale = 1
        self.robot_name = "quadruped"
        self.initial_pos = initial_pos
        self.initial_orn = initial_orn
        WalkerBase.__init__(self, 
                            "quadruped/minitaur.urdf", 
                            self.robot_name, 
                            action_dim=8, 
                            sensor_dim=OBSERVATION_DIM, 
                            power=2.5, 
                            scale = self.scale, 
                            target_pos=target_pos, 
                            resolution=resolution, 
                            mode=mode)
        self.minitaur = None
        self.num_motors = 8
        self.num_legs = int(self.num_motors / 2)
        self._self_collision_enabled = self_collision_enabled
        self._motor_velocity_limit = motor_velocity_limit
        self._pd_control_enabled = pd_control_enabled
        self._motor_direction = [-1, -1, -1, -1, 1, 1, 1, 1]
        self._observed_motor_torques = np.zeros(self.num_motors)
        self._applied_motor_torques = np.zeros(self.num_motors)
        self._max_force = 3.5
        self._accurate_motor_model_enabled = accurate_motor_model_enabled
        self._torque_control_enabled = torque_control_enabled
        self._motor_overheat_protection = motor_overheat_protection
        self._on_rack = on_rack
        self._joint_name_to_id = None
        if self._accurate_motor_model_enabled:
            self._kp = motor_kp
            self._kd = motor_kd
            self._motor_model = motor.MotorModel(torque_control_enabled=self._torque_control_enabled,kp=self._kp,kd=self._kd)
        elif self._pd_control_enabled:
            self._kp = 8
            self._kd = kd_for_pd_controllers
        else:
            self._kp = 1
            self._kd = 1

        self.time_step = time_step
        self.parts, self.jdict, self.ordered_joints, self.robot_body = None, None, None, None
        
        self.setup_keys_to_action()
        #self.robot_specific_reset()
    

    def _RecordMassInfoFromURDF(self):
        self._base_mass_urdf = p.getDynamicsInfo(self.minitaur, BASE_LINK_ID)[0]
        self._leg_masses_urdf = []
        self._leg_masses_urdf.append(p.getDynamicsInfo(self.minitaur, LEG_LINK_ID[0])[0])
        self._leg_masses_urdf.append(p.getDynamicsInfo(self.minitaur, MOTOR_LINK_ID[0])[0])

    def _BuildJointNameToIdDict(self):
        num_joints = p.getNumJoints(self.minitaur)
        self._joint_name_to_id = {}
        for i in range(num_joints):
            joint_info = p.getJointInfo(self.minitaur, i)
            self._joint_name_to_id[joint_info[1].decode("UTF-8")] = joint_info[0]

    def _BuildMotorIdList(self):
        self._motor_id_list = [
            self._joint_name_to_id[motor_name] for motor_name in MOTOR_NAMES
        ]


    def robot_specific_reset(self, reload_urdf=True):
        """Reset the minitaur to its initial states.

        Args:
          reload_urdf: Whether to reload the urdf file. If not, Reset() just place
            the minitaur back to its starting position.
        """

        if self.minitaur is None:
            self.minitaur = self.robot_ids[0]

        if self._joint_name_to_id is None:
            self._BuildJointNameToIdDict()
            self._BuildMotorIdList()
            self._RecordMassInfoFromURDF()
            self.ResetPose(add_constraint=True)
            self._overheat_counter = np.zeros(self.num_motors)
            self._motor_enabled_list = [True] * self.num_motors
            if self._on_rack:
                p.createConstraint(self.minitaur, -1, -1, -1, p.JOINT_FIXED,[0, 0, 0], [0, 0, 0], [0, 0, 1])

        #WalkerBase.robot_specific_reset(self)
        '''
        roll  = self.initial_orn[0]
        pitch = self.initial_orn[1]
        yaw   = self.initial_orn[2]
        self.robot_body.reset_orientation(quatWXYZ2quatXYZW(euler2quat(roll, pitch, yaw)))
        self.robot_body.reset_position(self.initial_pos)
        '''
        
        #p.resetBasePositionAndOrientation(self.minitaur, self.initial_pos, self.initial_orn)
        p.resetBasePositionAndOrientation(self.minitaur, self.initial_pos, [0, 0, 0, 1])
        p.resetBaseVelocity(self.minitaur, [0, 0, 0], [0, 0, 0])
        self.ResetPose(add_constraint=False)
        

        '''
        orientation, position = get_model_initial_pose("humanoid")
        roll  = orientation[0]
        pitch = orientation[1]
        yaw   = orientation[2]
        self.robot_body.reset_orientation(quatWXYZ2quatXYZW(euler2quat(roll, pitch, yaw)))
        self.robot_body.reset_position(position)
        '''

    def _SetMotorTorqueById(self, motor_id, torque):
        p.setJointMotorControl2(bodyIndex=self.minitaur,
                                jointIndex=motor_id,
                                controlMode=p.TORQUE_CONTROL,
                                force=torque)

    def _SetDesiredMotorAngleById(self, motor_id, desired_angle):
        p.setJointMotorControl2(bodyIndex=self.minitaur,
                                jointIndex=motor_id,
                                controlMode=p.POSITION_CONTROL,
                                targetPosition=desired_angle,
                                positionGain=self._kp,
                                velocityGain=self._kd,
                                force=self._max_force)

    def _SetDesiredMotorAngleByName(self, motor_name, desired_angle):
        self._SetDesiredMotorAngleById(self._joint_name_to_id[motor_name],desired_angle)

    def calc_potential(self):
        return 0


    def setup_keys_to_action(self):
        self.keys_to_action = {
            (ord('s'), ): 0, ## backward
            (ord('w'), ): 1, ## forward
            (ord('d'), ): 2, ## turn right
            (ord('a'), ): 3, ## turn left
            (): 4
        }

    
    def ResetPose(self, add_constraint):
        """Reset the pose of the minitaur.

        Args:
          add_constraint: Whether to add a constraint at the joints of two feet.
        """
        for i in range(self.num_legs):
            self._ResetPoseForLeg(i, add_constraint)

    def _ResetPoseForLeg(self, leg_id, add_constraint):
        """Reset the initial pose for the leg.

        Args:
          leg_id: It should be 0, 1, 2, or 3, which represents the leg at
            front_left, back_left, front_right and back_right.
          add_constraint: Whether to add a constraint at the joints of two feet.
        """
        knee_friction_force = 0
        half_pi = math.pi / 2.0
        knee_angle = -2.1834

        leg_position = LEG_POSITION[leg_id]
        p.resetJointState(
            self.minitaur,
            self._joint_name_to_id["motor_" + leg_position + "L_joint"],
            self._motor_direction[2 * leg_id] * half_pi,
            targetVelocity=0)
        p.resetJointState(
            self.minitaur,
            self._joint_name_to_id["knee_" + leg_position + "L_link"],
            self._motor_direction[2 * leg_id] * knee_angle,
            targetVelocity=0)
        p.resetJointState(
            self.minitaur,
            self._joint_name_to_id["motor_" + leg_position + "R_joint"],
            self._motor_direction[2 * leg_id + 1] * half_pi,
            targetVelocity=0)
        p.resetJointState(
            self.minitaur,
            self._joint_name_to_id["knee_" + leg_position + "R_link"],
            self._motor_direction[2 * leg_id + 1] * knee_angle,
            targetVelocity=0)
        if add_constraint:
            p.createConstraint(self.minitaur, 
                               self._joint_name_to_id["knee_" + leg_position + "R_link"],
                               self.minitaur, 
                               self._joint_name_to_id["knee_" + leg_position + "L_link"],
                               p.JOINT_POINT2POINT, 
                               [0, 0, 0],
                               KNEE_CONSTRAINT_POINT_RIGHT, 
                               KNEE_CONSTRAINT_POINT_LEFT)

        if self._accurate_motor_model_enabled or self._pd_control_enabled:
            # Disable the default motor in pybullet.
            p.setJointMotorControl2(bodyIndex=self.minitaur,
                                    jointIndex=(self._joint_name_to_id["motor_" + leg_position + "L_joint"]),
                                    controlMode=p.VELOCITY_CONTROL,
                                    targetVelocity=0,
                                    force=knee_friction_force)
            p.setJointMotorControl2(bodyIndex=self.minitaur,
                                    jointIndex=(self._joint_name_to_id["motor_" + leg_position + "R_joint"]),
                                    controlMode=p.VELOCITY_CONTROL,
                                    targetVelocity=0,
                                    force=knee_friction_force)

        else:
            self._SetDesiredMotorAngleByName("motor_" + leg_position + "L_joint",
                                             self._motor_direction[2 * leg_id] * half_pi)
            self._SetDesiredMotorAngleByName("motor_" + leg_position + "R_joint",
                                             self._motor_direction[2 * leg_id + 1] * half_pi)

        p.setJointMotorControl2(bodyIndex=self.minitaur,
                                jointIndex=(self._joint_name_to_id["knee_" + leg_position + "L_link"]),
                                controlMode=p.VELOCITY_CONTROL,
                                targetVelocity=0,
                                force=knee_friction_force)
        p.setJointMotorControl2(bodyIndex=self.minitaur,
                                jointIndex=(self._joint_name_to_id["knee_" + leg_position + "R_link"]),
                                controlMode=p.VELOCITY_CONTROL,
                                targetVelocity=0,
                                force=knee_friction_force)

    def GetBasePosition(self):
        """Get the position of minitaur's base.

        Returns:
          The position of minitaur's base.
        """
        position, _ = (
            p.getBasePositionAndOrientation(self.minitaur))
        return position

    def GetBaseOrientation(self):
        """Get the orientation of minitaur's base, represented as quaternion.

        Returns:
          The orientation of minitaur's base.
        """
        _, orientation = (
            p.getBasePositionAndOrientation(self.minitaur))
        return orientation

    def GetActionDimension(self):
        """Get the length of the action list.

        Returns:
          The length of the action list.
        """
        return self.num_motors

    def GetObservationUpperBound(self):
        """Get the upper bound of the observation.

        Returns:
          The upper bound of an observation. See GetObservation() for the details
            of each element of an observation.
        """
        upper_bound = np.array([0.0] * self.GetObservationDimension())
        upper_bound[0:self.num_motors] = math.pi  # Joint angle.
        upper_bound[self.num_motors:2 * self.num_motors] = (
            motor.MOTOR_SPEED_LIMIT)  # Joint velocity.
        upper_bound[2 * self.num_motors:3 * self.num_motors] = (
            motor.OBSERVED_TORQUE_LIMIT)  # Joint torque.
        upper_bound[3 * self.num_motors:] = 1.0  # Quaternion of base orientation.
        return upper_bound

    def GetObservationLowerBound(self):
        """Get the lower bound of the observation."""
        return -self.GetObservationUpperBound()

    def GetObservationDimension(self):
        """Get the length of the observation list.

        Returns:
          The length of the observation list.
        """
        return len(self.GetObservation())

    def calc_state(self):
        return self.GetObservation()

    def GetObservation(self):
        """Get the observations of minitaur.

        It includes the angles, velocities, torques and the orientation of the base.

        Returns:
          The observation list. observation[0:8] are motor angles. observation[8:16]
          are motor velocities, observation[16:24] are motor torques.
          observation[24:28] is the orientation of the base, in quaternion form.
        """
        observation = []
        observation.extend(self.GetMotorAngles().tolist())
        observation.extend(self.GetMotorVelocities().tolist())
        observation.extend(self.GetMotorTorques().tolist())
        observation.extend(list(self.GetBaseOrientation()))
        return observation

    def ApplyAction(self, motor_commands):
        """Set the desired motor angles to the motors of the minitaur.

        Note (hzyjerry): motor commands are set based on desired angles, not torques

        The desired motor angles are clipped based on the maximum allowed velocity.
        If the pd_control_enabled is True, a torque is calculated according to
        the difference between current and desired joint angle, as well as the joint
        velocity. This torque is exerted to the motor. For more information about
        PD control, please refer to: https://en.wikipedia.org/wiki/PID_controller.

        Args:
          motor_commands: The eight desired motor angles.
        """
        #print("motor commands", motor_commands)
        if self._motor_velocity_limit < np.inf:
            current_motor_angle = self.GetMotorAngles()
            motor_commands_max = (
                current_motor_angle + self.time_step * self._motor_velocity_limit)
            motor_commands_min = (
                current_motor_angle - self.time_step * self._motor_velocity_limit)
            motor_commands = np.clip(motor_commands, motor_commands_min, motor_commands_max)

        if self._accurate_motor_model_enabled or self._pd_control_enabled:
            q = self.GetMotorAngles()
            qdot = self.GetMotorVelocities()
            if self._accurate_motor_model_enabled:
                actual_torque, observed_torque = self._motor_model.convert_to_torque(
                    motor_commands, q, qdot)
                if self._motor_overheat_protection:
                    for i in range(self.num_motors):
                        if abs(actual_torque[i]) > OVERHEAT_SHUTDOWN_TORQUE:
                            self._overheat_counter[i] += 1
                        else:
                            self._overheat_counter[i] = 0
                        if (self._overheat_counter[i] >
                            OVERHEAT_SHUTDOWN_TIME / self.time_step):
                            self._motor_enabled_list[i] = False

                # The torque is already in the observation space because we use
                # GetMotorAngles and GetMotorVelocities.
                self._observed_motor_torques = observed_torque

                #actual_torque.fill(0.0)

                # Transform into the motor space when applying the torque.
                self._applied_motor_torque = np.multiply(actual_torque,
                                                         self._motor_direction)

                for motor_id, motor_torque, motor_enabled in zip(
                    self._motor_id_list, self._applied_motor_torque,
                    self._motor_enabled_list):
                    if motor_enabled:
                        print("pd torque", motor_torque)
                        self._SetMotorTorqueById(motor_id, motor_torque)
                    else:
                        self._SetMotorTorqueById(motor_id, 0)
            else:
                torque_commands = -self._kp * (q - motor_commands) - self._kd * qdot

                # The torque is already in the observation space because we use
                # GetMotorAngles and GetMotorVelocities.
                self._observed_motor_torques = torque_commands

                # Transform into the motor space when applying the torque.
                self._applied_motor_torques = np.multiply(self._observed_motor_torques,
                                                          self._motor_direction)

                for motor_id, motor_torque in zip(self._motor_id_list,
                                                  self._applied_motor_torques):
                    print("torque", motor_torque)
                    self._SetMotorTorqueById(motor_id, motor_torque)
        else:
            motor_commands_with_direction = np.multiply(motor_commands,
                                                      self._motor_direction)
            for motor_id, motor_command_with_direction in zip(
                self._motor_id_list, motor_commands_with_direction):
                print("command", motor_command_with_direction)
                self._SetDesiredMotorAngleById(motor_id, motor_command_with_direction)

    def GetMotorAngles(self):
        """Get the eight motor angles at the current moment.

        Returns:
          Motor angles.
        """
        motor_angles = [
            p.getJointState(self.minitaur, motor_id)[0]
            for motor_id in self._motor_id_list
        ]
        motor_angles = np.multiply(motor_angles, self._motor_direction)
        return motor_angles

    def GetMotorVelocities(self):
        """Get the velocity of all eight motors.

        Returns:
          Velocities of all eight motors.
        """
        motor_velocities = [
            p.getJointState(self.minitaur, motor_id)[1]
            for motor_id in self._motor_id_list
        ]
        motor_velocities = np.multiply(motor_velocities, self._motor_direction)
        return motor_velocities

    def GetMotorTorques(self):
        """Get the amount of torques the motors are exerting.

        Returns:
          Motor torques of all eight motors.
        """
        if self._accurate_motor_model_enabled or self._pd_control_enabled:
            return self._observed_motor_torques
        else:
            motor_torques = [
                p.getJointState(self.minitaur, motor_id)[3] for motor_id in self._motor_id_list ]
            motor_torques = np.multiply(motor_torques, self._motor_direction)
        return motor_torques

    def ConvertFromLegModel(self, actions):
        """Convert the actions that use leg model to the real motor actions.

        Args:
          actions: The theta, phi of the leg model.
        Returns:
          The eight desired motor angles that can be used in ApplyAction().
        """
        motor_angle = copy.deepcopy(actions)
        scale_for_singularity = 1
        offset_for_singularity = 1.5
        half_num_motors = int(self.num_motors / 2)
        quater_pi = math.pi / 4
        for i in range(self.num_motors):
            action_idx = i // 2
            forward_backward_component = (-scale_for_singularity * quater_pi * (
                actions[action_idx + half_num_motors] + offset_for_singularity))
            extension_component = (-1)**i * quater_pi * actions[action_idx]
            if i >= half_num_motors:
                extension_component = -extension_component
            motor_angle[i] = (
                math.pi + forward_backward_component + extension_component)
        return motor_angle

    def GetBaseMassFromURDF(self):
        """Get the mass of the base from the URDF file."""
        return self._base_mass_urdf

    def GetLegMassesFromURDF(self):
        """Get the mass of the legs from the URDF file."""
        return self._leg_masses_urdf

    def SetBaseMass(self, base_mass):
        p.changeDynamics(
            self.minitaur, BASE_LINK_ID, mass=base_mass)

    def SetLegMasses(self, leg_masses):
        """Set the mass of the legs.

        A leg includes leg_link and motor. All four leg_links have the same mass,
        which is leg_masses[0]. All four motors have the same mass, which is
        leg_mass[1].

        Args:
          leg_masses: The leg masses. leg_masses[0] is the mass of the leg link.
            leg_masses[1] is the mass of the motor.
        """
        for link_id in LEG_LINK_ID:
            p.changeDynamics(
                self.minitaur, link_id, mass=leg_masses[0])
        for link_id in MOTOR_LINK_ID:
            p.changeDynamics(
                self.minitaur, link_id, mass=leg_masses[1])

    def SetFootFriction(self, foot_friction):
        """Set the lateral friction of the feet.

        Args:
          foot_friction: The lateral friction coefficient of the foot. This value is
            shared by all four feet.
        """
        for link_id in FOOT_LINK_ID:
            p.changeDynamics(
                self.minitaur, link_id, lateralFriction=foot_friction)

    def SetBatteryVoltage(self, voltage):
        if self._accurate_motor_model_enabled:
            self._motor_model.set_voltage(voltage)

    def SetMotorViscousDamping(self, viscous_damping):
        if self._accurate_motor_model_enabled:
            self._motor_model.set_viscous_damping(viscous_damping)



class Minitaur(MinitaurBase):
    '''Wrapper class for gibson interface
    
    Attribtues:
        self.eyes
        sekf.eye_offset_orn
        self.resolution
        self.walk_target_x, self.walk_target_y
        self.mjcf_scaling
        self.observation_space
        self.action_space
        self.sensor_space

    Interface:
        self.apply_action()
        self.calc_state()
        self.addToScene()
    '''
    def __init__(self,
                 time_step=0.01,
                 self_collision_enabled=True,
                 motor_velocity_limit=np.inf,
                 pd_control_enabled=False,
                 accurate_motor_model_enabled=True,   ## (hzyjerry): affect speed?
                 motor_kp=1.20, #1.0,
                 motor_kd=0.00, #0.02,
                 torque_control_enabled=False,
                 motor_overheat_protection=False,
                 on_rack=False,
                 kd_for_pd_controllers=0.3, 

                 ## Below: for gibson
                 is_discrete=False, 
                 initial_pos=INIT_POSITION, 
                 initial_orn=INIT_ORIENTATION, 
                 target_pos=[1, 0, 0], 
                 resolution="NORMAL", 
                 mode="RGB"):

        self.mjcf_scaling = 1     ## place holder

        MinitaurBase.__init__(self,
                     self_collision_enabled=self_collision_enabled,
                     motor_velocity_limit=motor_velocity_limit,
                     pd_control_enabled=pd_control_enabled,
                     accurate_motor_model_enabled=accurate_motor_model_enabled,   ## (hzyjerry): affect speed?
                     motor_kp=motor_kp,
                     motor_kd=motor_kd,
                     torque_control_enabled=torque_control_enabled,
                     motor_overheat_protection=motor_overheat_protection,
                     on_rack=on_rack,
                     kd_for_pd_controllers=kd_for_pd_controllers, 

                     ## Below: for gibson
                     is_discrete=is_discrete, 
                     initial_pos=initial_pos, 
                     initial_orn=initial_orn, 
                     target_pos=target_pos, 
                     resolution=resolution, 
                     mode=mode)

    def calc_state(self):
        MinitaurBase.GetObservation(self)

    def apply_action(self, action):
        MinitaurBase.ApplyAction(self, action)