from argparse import ArgumentParser, _SubParsersAction

from pipeline.console import cluster, container, logs
from pipeline.console.targets import (
    files,
    pipelines,
    pointers,
    resources,
    scaling_configs,
)


def create_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    create_parser = command_parser.add_parser(
        "create",
        description="Create a new object.",
        help="Create a new object.",
    )
    create_parser.set_defaults(func=lambda _: create_parser.print_help())

    create_sub_parser = create_parser.add_subparsers(
        dest="target",
    )

    pointers.create_parser(create_sub_parser)
    resources.create_parser(create_sub_parser)
    files.create_parser(create_sub_parser)
    scaling_configs.create_parser(create_sub_parser)


def edit_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    edit_parser = command_parser.add_parser(
        "edit",
        description="Edit an object.",
        help="Edit and object.",
    )
    edit_parser.set_defaults(func=lambda _: edit_parser.print_help())

    edit_sub_parser = edit_parser.add_subparsers(
        dest="target",
    )

    pipelines.edit_parser(edit_sub_parser)
    pointers.edit_parser(edit_sub_parser)
    files.edit_parser(edit_sub_parser)
    scaling_configs.edit_parser(edit_sub_parser)


def get_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    get_parser = command_parser.add_parser(
        "get",
        description="Get an object(s).",
        help="Get an object(s).",
    )
    get_parser.set_defaults(func=lambda _: get_parser.print_help())

    get_sub_parser = get_parser.add_subparsers(
        dest="target",
    )

    pipelines.get_parser(get_sub_parser)
    pointers.get_parser(get_sub_parser)
    resources.get_parser(get_sub_parser)
    files.get_parser(get_sub_parser)
    scaling_configs.get_parser(get_sub_parser)


def delete_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    delete_parser = command_parser.add_parser(
        "delete",
        aliases=["del", "rm"],
        description="Delete an object.",
        help="Delete an object.",
    )
    delete_parser.set_defaults(func=lambda _: delete_parser.print_help())

    delete_sub_parser = delete_parser.add_subparsers(
        dest="target",
    )

    pipelines.delete_parser(delete_sub_parser)
    pointers.delete_parser(delete_sub_parser)
    resources.delete_parser(delete_sub_parser)
    files.delete_parser(delete_sub_parser)
    scaling_configs.delete_parser(delete_sub_parser)


def cluster_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    cluster_parser = command_parser.add_parser(
        "cluster",
        aliases=["cl"],
        description="Manage compute clusters.",
        help="Manage compute clusters.",
    )
    cluster_parser.set_defaults(func=lambda _: cluster_parser.print_help())

    cluster_sub_parser = cluster_parser.add_subparsers(
        dest="target",
    )

    cluster.login_parser(cluster_sub_parser)
    cluster.use_parser(cluster_sub_parser)
    cluster.remove_parser(cluster_sub_parser)
    cluster.get_parser(cluster_sub_parser)


def logs_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    logs_parser = command_parser.add_parser(
        "logs",
        description="Get logs.",
        help="Get logs.",
    )
    logs_parser.set_defaults(func=lambda _: logs_parser.print_help())
    logs_sub_parser = logs_parser.add_subparsers(
        dest="target",
    )
    logs.run_logs_parser(logs_sub_parser)
    logs.pipeline_startup_logs_parser(logs_sub_parser)


def container_parser(command_parser: "_SubParsersAction[ArgumentParser]") -> None:
    container_parser = command_parser.add_parser(
        "container",
        description="Manage pipeline containers.",
        help="Manage pipeline containers.",
    )
    container_parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="Filepath to a pipeline configuration yaml file.",
        default="./pipeline.yaml",
    )
    container_parser.set_defaults(func=lambda _: container_parser.print_help())
    container_sub_parser = container_parser.add_subparsers(
        dest="target",
    )

    build_parser = container_sub_parser.add_parser(
        "build",
        description="Build a pipeline container.",
        help="Build a pipeline container.",
    )
    build_parser.set_defaults(func=container.build_container)

    build_parser.add_argument(
        "--docker-file",
        "-d",
        type=str,
        help="Filepath to a custom Dockerfile. No dockerfile will be auto-generated!",  # noqa
    )

    push_parser = container_sub_parser.add_parser(
        "push",
        description="Push a pipeline container.",
        help="Push a pipeline container.",
    )
    push_parser.set_defaults(func=container.push_container)
    push_parser.add_argument(
        "--pointer",
        "-p",
        action="append",
        help="Pointer for the container.",
    )
    push_parser.add_argument(
        "--pointer-overwrite",
        "-o",
        action="store_true",
        help="Overwrite existing pointers.",
    )
    push_parser.add_argument(
        "--cluster",
        type=str,
        help="Cluster ID for the pipeline container.",
    )
    push_parser.add_argument(
        "--node-pool",
        type=str,
        help="Node pool name for the pipeline container.",
    )

    up_parser = container_sub_parser.add_parser(
        "up",
        description="Start a pipeline container.",
        help="Start a pipeline container.",
    )
    up_parser.set_defaults(func=container.up_container)
    up_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Start the container in debug mode.",
    )
    # Allow multiple volumes to be specified

    up_parser.add_argument(
        "--volume",
        "-v",
        action="append",
        help="Mount a volume into the container.",
    )

    up_parser.add_argument(
        "--port",
        "-p",
        help="Container port.",
        default=14300,
    )
    # Init
    init_parser = container_sub_parser.add_parser(
        "init",
        description="Initialize a directory for a new pipeline.",
        help="Initialize a directory for a new pipeline.",
    )

    init_parser.add_argument(
        "--name",
        "-n",
        type=str,
        help="Name of the pipeline.",
    )
    init_parser.set_defaults(func=container.init_dir)

    # Convert
    convert_parser = container_sub_parser.add_parser(
        "convert",
        description=(
            "Create a new pipeline by converting a model from a different framework."
        ),
        help="Create a new pipeline by converting a model from a different framework.",
    )

    convert_parser.add_argument(
        "--name",
        "-n",
        type=str,
        help="Name of the pipeline.",
    )
    convert_parser.add_argument(
        "--type", "-t", type=str, help="Type of model", choices=["cog"], required=True
    )
    convert_parser.set_defaults(func=container.convert)
