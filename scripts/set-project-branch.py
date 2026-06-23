#!/usr/bin/env python3
"""Set or insert the Maven project.branch property in a pom.xml file."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def tag_name(element: ET.Element) -> str:
    if element.tag.startswith("{"):
        return element.tag.rsplit("}", 1)[1]
    return element.tag


def namespace(element: ET.Element) -> str:
    if element.tag.startswith("{"):
        return element.tag[1:].split("}", 1)[0]
    return ""


def qualified(name: str, ns: str) -> str:
    return f"{{{ns}}}{name}" if ns else name


def child(parent: ET.Element, name: str) -> ET.Element | None:
    for item in parent:
        if tag_name(item) == name:
            return item
    return None


def insert_properties(root: ET.Element, ns: str) -> ET.Element:
    properties = ET.Element(qualified("properties", ns))
    preferred_after = {"version", "artifactId", "groupId", "parent", "modelVersion"}

    insert_at = 0
    for index, item in enumerate(list(root)):
        if tag_name(item) in preferred_after:
            insert_at = index + 1

    root.insert(insert_at, properties)
    return properties


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: set-project-branch.py <pom.xml> <project.branch>", file=sys.stderr)
        return 2

    pom_path = Path(sys.argv[1])
    branch_value = sys.argv[2].strip()
    if not branch_value:
        print("project.branch cannot be empty", file=sys.stderr)
        return 2

    tree = ET.parse(pom_path)
    root = tree.getroot()
    ns = namespace(root)
    if ns:
        ET.register_namespace("", ns)

    properties = child(root, "properties")
    if properties is None:
        properties = insert_properties(root, ns)

    project_branch = child(properties, "project.branch")
    if project_branch is None:
        project_branch = ET.SubElement(properties, qualified("project.branch", ns))

    project_branch.text = branch_value
    ET.indent(tree, space="  ")
    tree.write(pom_path, encoding="utf-8", xml_declaration=False)
    pom_path.write_text(pom_path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
