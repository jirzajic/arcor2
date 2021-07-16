import time

from arcor2.data.common import Pose
from arcor2.data.object_type import Mesh
from arcor2.data.rpc.common import RobotArg
from arcor2.object_types.upload import upload_def
from arcor2.test_objects.box import Box
from arcor2.test_objects.dummy_multiarm_robot import DummyMultiArmRobot
from arcor2_arserver.tests.conftest import event, lock_object, project_service
from arcor2_arserver_data import events, rpc
from arcor2_arserver_data.client import ARServer, get_id


def test_object_aiming(start_processes: None, ars: ARServer) -> None:

    upload_def(Box)

    # assign mesh to some existing object
    ot = project_service.get_object_type(Box.__name__)

    project_service.upload_file("mesh.dae", b"")

    mesh = Mesh(ot.id, "mesh.dae", [Pose(), Pose(), Pose()])
    assert mesh.focus_points
    project_service.put_model(mesh)

    ot.model = mesh.metamodel()
    project_service.update_object_type(ot)

    upload_def(DummyMultiArmRobot)

    test = "test"

    event(ars, events.c.ShowMainScreen)

    time.sleep(1)  # otherwise ARServer might not notice new ObjectTypes

    assert ars.call_rpc(
        rpc.s.NewScene.Request(get_id(), rpc.s.NewScene.Request.Args(test)), rpc.s.NewScene.Response
    ).result

    assert len(event(ars, events.o.ChangedObjectTypes).data) == 2

    event(ars, events.s.OpenScene)
    event(ars, events.s.SceneState)

    assert ars.call_rpc(
        rpc.s.AddObjectToScene.Request(get_id(), rpc.s.AddObjectToScene.Request.Args("obj", Box.__name__, Pose())),
        rpc.s.AddObjectToScene.Response,
    ).result

    scene_obj = event(ars, events.s.SceneObjectChanged).data

    assert ars.call_rpc(
        rpc.s.AddObjectToScene.Request(
            get_id(), rpc.s.AddObjectToScene.Request.Args("robot", DummyMultiArmRobot.__name__, Pose())
        ),
        rpc.s.AddObjectToScene.Response,
    ).result

    scene_robot = event(ars, events.s.SceneObjectChanged).data

    assert ars.call_rpc(
        rpc.s.StartScene.Request(get_id()),
        rpc.s.StartScene.Response,
    ).result

    assert event(ars, events.s.SceneState).data.state == events.s.SceneState.Data.StateEnum.Starting
    assert event(ars, events.s.SceneState).data.state == events.s.SceneState.Data.StateEnum.Started

    arm = DummyMultiArmRobot.Arms.left
    robot_arg = RobotArg(scene_robot.id, list(DummyMultiArmRobot.EEF[arm])[0], arm)

    # ------------------------------------------------------------------------------------------------------------------

    lock_object(ars, scene_obj.id)
    lock_object(ars, scene_robot.id)

    assert ars.call_rpc(
        rpc.o.ObjectAimingStart.Request(get_id(), rpc.o.ObjectAimingStart.Request.Args(scene_obj.id, robot_arg)),
        rpc.o.ObjectAimingStart.Response,
    ).result

    assert not ars.call_rpc(
        rpc.o.ObjectAimingStart.Request(get_id(), rpc.o.ObjectAimingStart.Request.Args(scene_obj.id, robot_arg)),
        rpc.o.ObjectAimingStart.Response,
    ).result

    assert ars.call_rpc(
        rpc.o.ObjectAimingAddPoint.Request(get_id(), rpc.o.ObjectAimingAddPoint.Request.Args(0)),
        rpc.o.ObjectAimingAddPoint.Response,
    ).result

    assert ars.call_rpc(
        rpc.o.ObjectAimingCancel.Request(get_id()),
        rpc.o.ObjectAimingCancel.Response,
    ).result

    assert set(event(ars, events.lk.ObjectsUnlocked).data.object_ids) == {scene_robot.id, scene_obj.id}

    # ------------------------------------------------------------------------------------------------------------------

    lock_object(ars, scene_obj.id)
    lock_object(ars, scene_robot.id)

    assert ars.call_rpc(
        rpc.o.ObjectAimingStart.Request(get_id(), rpc.o.ObjectAimingStart.Request.Args(scene_obj.id, robot_arg)),
        rpc.o.ObjectAimingStart.Response,
    ).result

    assert not ars.call_rpc(
        rpc.o.ObjectAimingAddPoint.Request(get_id(), rpc.o.ObjectAimingAddPoint.Request.Args(-1)),
        rpc.o.ObjectAimingAddPoint.Response,
    ).result

    assert not ars.call_rpc(
        rpc.o.ObjectAimingAddPoint.Request(get_id(), rpc.o.ObjectAimingAddPoint.Request.Args(len(mesh.focus_points))),
        rpc.o.ObjectAimingAddPoint.Response,
    ).result

    assert not ars.call_rpc(
        rpc.o.ObjectAimingDone.Request(get_id()),
        rpc.o.ObjectAimingDone.Response,
    ).result

    for idx in range(len(mesh.focus_points)):

        assert ars.call_rpc(
            rpc.o.ObjectAimingAddPoint.Request(get_id(), rpc.o.ObjectAimingAddPoint.Request.Args(idx)),
            rpc.o.ObjectAimingAddPoint.Response,
        ).result

    assert ars.call_rpc(
        rpc.o.ObjectAimingDone.Request(get_id()),
        rpc.o.ObjectAimingDone.Response,
    ).result

    assert event(ars, events.lk.ObjectsUnlocked).data.object_ids == [scene_robot.id]
    assert event(ars, events.s.SceneObjectChanged).data.id == scene_obj.id
    assert event(ars, events.lk.ObjectsUnlocked).data.object_ids == [scene_obj.id]
