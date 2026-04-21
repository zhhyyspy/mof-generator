"""
Seed a V3.0 抽水蓄能电站 (Pumped-Storage Plant) demo:

  M2 元结构 (4 levels):   设施 → 功能分组 → 设备 → 部件
  M1 领域层 (~28 类):     完整的电站→功能分组→设备→部件分类树
  M1 compositions:         ~30 条,体现跨层级 + 同层级的物理/逻辑包含关系

The demo is designed to let the new "M1 元结构树形面板" light up:
— node per M1 class, column per M2 level, arrows = M1 compositions.

Run once:
    C:/Python314/python.exe scripts/seed_pumped_storage.py
"""
from __future__ import annotations

import sys, uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.models.m1_model import M1Model, M1ModelVersion as ModelVersion  # noqa: E402
from backend.models.m3_schema import (  # noqa: E402
    Package, MOFClass, Attribute, Multiplicity,
    Association, AssociationEnd, StructuralPattern,
)
from backend.storage.file_store import FileStore  # noqa: E402


# IDs must follow the `m2_` prefix convention recognized by the frontend
M1_ID = "pumped_storage"
M2_ID = f"m2_{M1_ID}"

NS = uuid.UUID("a1b2c3d4-5678-9abc-def0-123456789abc")
def uid(tag: str) -> str:
    return str(uuid.uuid5(NS, tag))


def mult(lo: int, hi: int) -> Multiplicity:
    return Multiplicity(lower=lo, upper=hi)


def attr(name: str, label: str, dtype: str = "String", *,
         lo: int = 0, hi: int = 1, unit: str | None = None,
         desc: str | None = None, inherited: bool = False,
         tag_prefix: str = "") -> Attribute:
    return Attribute(
        id=uid(f"attr/{tag_prefix}/{name}"),
        name=name, label=label, description=desc,
        data_type=dtype, multiplicity=mult(lo, hi),
        unit=unit, is_inherited=inherited,
    )


PATTERN_ID = uid("m2/pattern/PumpedStorage")


# ============================================================================
#                          M2  (Pumped-Storage Metamodel)
# ============================================================================

FAC_ID = uid("m2/class/Facility")
facility = MOFClass(
    id=FAC_ID, name="Facility", label="设施",
    description="独立运行的水工/电力设施,此处用于抽水蓄能电站整体。元结构根节点。",
    attributes=[
        attr("facilityCode", "设施编号", "String", lo=1, hi=1, tag_prefix="Facility"),
        attr("facilityName", "设施名称", "String", lo=1, hi=1, tag_prefix="Facility"),
        attr("location", "地理位置", "String", lo=0, hi=1, tag_prefix="Facility"),
        attr("installedCapacity", "总装机容量", "Float", lo=0, hi=1, unit="MW", tag_prefix="Facility"),
        attr("builtYear", "投运年份", "Integer", lo=0, hi=1, tag_prefix="Facility"),
        attr("managerOrg", "管理单位", "String", lo=0, hi=1, tag_prefix="Facility"),
    ],
    meta_structure_id=PATTERN_ID, meta_structure_role="root", meta_structure_level=1,
)

FG_ID = uid("m2/class/FunctionalGroup")
func_group = MOFClass(
    id=FG_ID, name="FunctionalGroup", label="功能分组",
    description="设施内按功能划分的区域/系统/机组等中间层级,支持任意深度的组合。",
    attributes=[
        attr("groupCode", "分组编号", "String", lo=1, hi=1, tag_prefix="FunctionalGroup"),
        attr("groupName", "分组名称", "String", lo=1, hi=1, tag_prefix="FunctionalGroup"),
        attr("functionDesc", "功能描述", "String", lo=0, hi=1, tag_prefix="FunctionalGroup"),
        attr("runStatus", "运行状态", "String", lo=0, hi=1,
             desc="运行中/检修/停用/备用", tag_prefix="FunctionalGroup"),
    ],
    meta_structure_id=PATTERN_ID, meta_structure_role="intermediate", meta_structure_level=2,
)

EQP_ID = uid("m2/class/Equipment")
equipment = MOFClass(
    id=EQP_ID, name="Equipment", label="设备",
    description="独立可维护的设备单元。",
    attributes=[
        attr("equipCode", "设备编号", "String", lo=1, hi=1, tag_prefix="Equipment"),
        attr("equipName", "设备名称", "String", lo=1, hi=1, tag_prefix="Equipment"),
        attr("manufacturer", "制造厂家", "String", lo=0, hi=1, tag_prefix="Equipment"),
        attr("modelNo", "型号", "String", lo=0, hi=1, tag_prefix="Equipment"),
        attr("installedAt", "安装日期", "Date", lo=0, hi=1, tag_prefix="Equipment"),
        attr("ratedPower", "额定功率", "Float", lo=0, hi=1, unit="MW", tag_prefix="Equipment"),
    ],
    meta_structure_id=PATTERN_ID, meta_structure_role="intermediate", meta_structure_level=3,
)

COMP_ID = uid("m2/class/Component")
component = MOFClass(
    id=COMP_ID, name="Component", label="部件",
    description="设备内部可更换/可检修的零部件。",
    attributes=[
        attr("partNo", "部件编号", "String", lo=1, hi=1, tag_prefix="Component"),
        attr("partName", "部件名称", "String", lo=1, hi=1, tag_prefix="Component"),
        attr("partSpec", "规格型号", "String", lo=0, hi=1, tag_prefix="Component"),
        attr("qty", "数量", "Integer", lo=1, hi=1, tag_prefix="Component"),
        attr("replaceable", "可更换", "Boolean", lo=0, hi=1, tag_prefix="Component"),
    ],
    meta_structure_id=PATTERN_ID, meta_structure_role="leaf", meta_structure_level=4,
)


def hier_assoc(src_id, src_name, tgt_id, tgt_name, role_src, role_tgt, name, label, order):
    return Association(
        id=uid(f"m2/assoc/{name}"), name=name, label=label,
        source=AssociationEnd(class_ref=src_id, class_name=src_name,
                              role_name=role_src, multiplicity=mult(1, 1), navigable=True),
        target=AssociationEnd(class_ref=tgt_id, class_name=tgt_name,
                              role_name=role_tgt, multiplicity=mult(0, -1), navigable=True),
        association_type="composition", is_hierarchy=True, hierarchy_order=order,
    )


hier_fg = hier_assoc(FAC_ID, "Facility", FG_ID, "FunctionalGroup",
                     "facility", "groups", "facilityHasGroup", "设施包含功能分组", 1)
hier_ge = hier_assoc(FG_ID, "FunctionalGroup", EQP_ID, "Equipment",
                     "group", "equipments", "groupHasEquipment", "分组包含设备", 2)
hier_ec = hier_assoc(EQP_ID, "Equipment", COMP_ID, "Component",
                     "equipment", "components", "equipmentHasComponent", "设备包含部件", 3)

pattern = StructuralPattern(
    id=PATTERN_ID, name="PumpedStoragePattern",
    label="抽水蓄能电站层级模板",
    description="抽水蓄能电站标准 4 级元结构: 设施 → 功能分组 → 设备 → 部件。"
                "功能分组允许嵌套(如 机组区域 下含 1号机组)。",
    participating_class_ids=[FAC_ID, FG_ID, EQP_ID, COMP_ID],
    hierarchy_association_ids=[hier_fg.id, hier_ge.id, hier_ec.id],
    root_class_id=FAC_ID,
    level_names=["L1-设施", "L2-功能分组", "L3-设备", "L4-部件"],
    constraints=["no_cycle", "no_cross_level", "no_reverse", "root_fixed"],
    recommended_assoc_type="composition",
)

m2_pkg = Package(
    id=uid("m2/pkg"), name="PumpedStorageMetaModel",
    label="抽水蓄能电站 M2 元模型",
    description="4 级元结构,功能分组支持嵌套。",
    classes=[facility, func_group, equipment, component],
    associations=[hier_fg, hier_ge, hier_ec],
    structural_patterns=[pattern],
    publish_status="published",
    published_at=datetime.utcnow().isoformat(), published_by="seed-script",
)

m2_model = M1Model(
    id=M2_ID, name="PumpedStorageM2",
    label="抽水蓄能电站 · M2 (4 级元结构)",
    description="抽水蓄能行业元模型,功能分组支持嵌套(设施→机组区域→1号机组→设备)。",
    m2_template_id="",
    current_version="1.0",
    versions=[ModelVersion(
        version="1.0", created_at=datetime.utcnow().isoformat(),
        created_by="seed-script", changelog="初始版本",
        package=m2_pkg,
    )],
    status="published",
)


# ============================================================================
#                          M1  (Domain classes)
# ============================================================================

def inherit(parent_cls: MOFClass, tag: str) -> list[Attribute]:
    out = []
    for a in parent_cls.attributes:
        out.append(Attribute(
            id=uid(f"m1/{tag}/inherit/{a.name}"),
            name=a.name, label=a.label, description=a.description,
            data_type=a.data_type, enum_ref=a.enum_ref, complex_type_ref=a.complex_type_ref,
            multiplicity=Multiplicity(lower=a.multiplicity.lower, upper=a.multiplicity.upper),
            unit=a.unit, default_value=a.default_value, is_inherited=True,
        ))
    return out


def m1cls(tag: str, label: str, parent: MOFClass, desc: str, own_attrs: list[Attribute]) -> MOFClass:
    return MOFClass(
        id=uid(f"m1/class/{tag}"), name=tag, label=label, description=desc,
        parent_class_ref=parent.id, parent_class_name=parent.name,
        attributes=inherit(parent, tag) + own_attrs,
    )


# ---------- L1 设施 ----------
plant = m1cls("PumpedStoragePlant", "抽蓄电站", facility,
    "抽水蓄能电站整体,含上下水库、输水系统、厂房机电、开关站等全部功能分组。",
    [
        attr("upperReservoirCap", "上水库库容", "Float", unit="万m³", tag_prefix="PumpedStoragePlant"),
        attr("lowerReservoirCap", "下水库库容", "Float", unit="万m³", tag_prefix="PumpedStoragePlant"),
        attr("rateHead", "额定水头", "Float", unit="m", tag_prefix="PumpedStoragePlant"),
        attr("numUnits", "机组台数", "Integer", tag_prefix="PumpedStoragePlant"),
    ])

# ---------- L2 功能分组 (嵌套) ----------
unit_zone = m1cls("UnitZone", "机组区域", func_group,
    "厂房内所有发电/抽水机组的集合区域,含桥式起重机、机组辅助系统等。",
    [attr("zoneArea", "区域面积", "Float", unit="m²", tag_prefix="UnitZone")])

ps_unit = m1cls("PumpedStorageUnit", "抽蓄机组", func_group,
    "可逆式水泵水轮机组,含水轮机、电动机、球阀、调速器、励磁等子设备。1号/2号...机组均 instanceOf 该类。",
    [
        attr("unitRatedPower", "机组额定功率", "Float", unit="MW", tag_prefix="PumpedStorageUnit"),
        attr("unitOperationMode", "运行模式", "String",
             desc="发电/抽水/调相/停机", tag_prefix="PumpedStorageUnit"),
    ])

waterway = m1cls("ConveyanceSystem", "输水系统", func_group,
    "上水库→厂房的压力输水系统,含引水隧洞、压力钢管、尾水。",
    [attr("waterwayLength", "输水总长", "Float", unit="m", tag_prefix="ConveyanceSystem")])

intake_zone = m1cls("IntakeZone", "进水口区", func_group,
    "上水库侧进/出水口建筑物与拦污栅、进水闸门等。",
    [attr("intakeElev", "进水口高程", "Float", unit="m", tag_prefix="IntakeZone")])

switchyard = m1cls("SwitchyardZone", "开关站区", func_group,
    "电站升压并网用的开关站,含主变压器、GIS、二次系统。",
    [attr("yardVoltage", "出线电压", "Float", unit="kV", tag_prefix="SwitchyardZone")])

aux_zone = m1cls("AuxiliarySystemZone", "辅助系统区", func_group,
    "水、风、油、消防、通风空调等厂房辅助系统集中区。",
    [])

# ---------- L3 设备 ----------
pump_turbine = m1cls("PumpTurbine", "水泵水轮机", equipment,
    "可逆式水泵水轮机,双向运行(发电/抽水)。",
    [
        attr("runnerType", "转轮型式", "String",
             desc="混流可逆式/斜流/贯流", tag_prefix="PumpTurbine"),
        attr("ratedHead", "额定水头", "Float", unit="m", tag_prefix="PumpTurbine"),
        attr("ratedFlow", "额定流量", "Float", unit="m³/s", tag_prefix="PumpTurbine"),
    ])

gen_motor = m1cls("GeneratorMotor", "发电电动机", equipment,
    "同步发电电动机,发电与电动工况双向运行。",
    [
        attr("ratedVoltage", "额定电压", "Float", unit="kV", tag_prefix="GeneratorMotor"),
        attr("ratedSpeed", "额定转速", "Float", unit="r/min", tag_prefix="GeneratorMotor"),
        attr("powerFactor", "功率因数", "Float", tag_prefix="GeneratorMotor"),
    ])

inlet_valve = m1cls("SphericalInletValve", "进水球阀", equipment,
    "机组前主阀,用于机组启停与事故关断。",
    [attr("valveDiameter", "阀体直径", "Float", unit="mm", tag_prefix="SphericalInletValve")])

governor = m1cls("GovernorSystem", "调速器", equipment,
    "水轮机导叶/喷嘴控制系统。",
    [attr("governorType", "调速方式", "String", tag_prefix="GovernorSystem")])

exciter = m1cls("ExcitationSystem", "励磁系统", equipment,
    "发电电动机的励磁调节系统,含整流柜。",
    [attr("excitationType", "励磁方式", "String", tag_prefix="ExcitationSystem")])

main_transformer = m1cls("MainTransformer", "主变压器", equipment,
    "机端升压变压器,连接机组出口到开关站。",
    [attr("transformerCap", "额定容量", "Float", unit="MVA", tag_prefix="MainTransformer")])

gis = m1cls("GISSwitchgear", "GIS高压开关", equipment,
    "气体绝缘金属封闭开关设备,电站出线侧高压配电。",
    [attr("rateVoltageKV", "额定电压", "Float", unit="kV", tag_prefix="GISSwitchgear")])

crane = m1cls("OverheadCrane", "桥式起重机", equipment,
    "厂房行车,用于机组吊装检修。",
    [attr("liftCapacity", "起重量", "Float", unit="t", tag_prefix="OverheadCrane")])

intake_gate = m1cls("IntakeGate", "进水闸门", equipment,
    "进水口启闭控制闸门。",
    [attr("gateWidth", "闸孔净宽", "Float", unit="m", tag_prefix="IntakeGate")])

penstock = m1cls("PenstockPipe", "压力钢管", equipment,
    "输水系统主要承压管道。",
    [
        attr("pipeDiameter", "管径", "Float", unit="m", tag_prefix="PenstockPipe"),
        attr("pipeThickness", "壁厚", "Float", unit="mm", tag_prefix="PenstockPipe"),
    ])

aux_pump = m1cls("AuxiliaryPump", "辅机水泵", equipment,
    "充水/排水/冷却等辅助系统水泵。",
    [attr("pumpDuty", "用途", "String", tag_prefix="AuxiliaryPump")])

# ---------- L4 部件 ----------
runner = m1cls("Runner", "转轮", component,
    "水泵水轮机核心水力部件。",
    [attr("runnerMaterial", "转轮材质", "String", tag_prefix="Runner")])

main_shaft = m1cls("MainShaft", "主轴", component,
    "机组水轮机与发电电动机之间的联轴段。",
    [])

guide_bearing = m1cls("GuideBearing", "导轴承", component,
    "承受机组径向力的滑动/稀油轴承。",
    [attr("bearingDiameter", "轴径", "Float", unit="mm", tag_prefix="GuideBearing")])

thrust_bearing = m1cls("ThrustBearing", "推力轴承", component,
    "承受机组轴向推力的推力瓦。",
    [])

stator_coil = m1cls("StatorCoil", "定子线圈", component,
    "发电电动机定子绕组线圈。",
    [attr("coilPhase", "相", "String", desc="A/B/C", tag_prefix="StatorCoil")])

rotor_pole = m1cls("RotorPole", "转子磁极", component,
    "发电电动机转子磁极,含磁极线圈。",
    [])

cooler = m1cls("Cooler", "冷却器", component,
    "空气冷却器 / 油冷却器。",
    [attr("coolerMedium", "冷却介质", "String", tag_prefix="Cooler")])

exc_rectifier = m1cls("ExcitationRectifier", "励磁整流柜", component,
    "励磁系统的晶闸管整流装置。",
    [])

seal_ring = m1cls("SealRing", "密封环", component,
    "转动与静止部件之间的密封件。",
    [])


# ---------- M1 compositions (关键:体现用户示例的树形包含) ----------
def comp(src: MOFClass, tgt: MOFClass, role_src: str, role_tgt: str, name: str, label: str):
    return Association(
        id=uid(f"m1/assoc/{name}"), name=name, label=label,
        source=AssociationEnd(class_ref=src.id, class_name=src.name,
                              role_name=role_src, multiplicity=mult(1, 1), navigable=True),
        target=AssociationEnd(class_ref=tgt.id, class_name=tgt.name,
                              role_name=role_tgt, multiplicity=mult(0, -1), navigable=True),
        association_type="composition", is_hierarchy=False,
    )


m1_associations = [
    # 电站 → 各功能分组 (L1→L2)
    comp(plant, unit_zone,    "plant", "unitZone",        "plantHasUnitZone",    "电站包含机组区域"),
    comp(plant, intake_zone,  "plant", "intakeZone",      "plantHasIntakeZone",  "电站包含进水口区"),
    comp(plant, waterway,     "plant", "conveyance",      "plantHasConveyance",  "电站包含输水系统"),
    comp(plant, switchyard,   "plant", "switchyard",      "plantHasSwitchyard",  "电站包含开关站"),
    comp(plant, aux_zone,     "plant", "auxZone",         "plantHasAuxZone",     "电站包含辅助系统区"),
    # 机组区域 → 抽蓄机组 (L2 同层级嵌套)
    comp(unit_zone, ps_unit,  "zone",  "units",           "zoneHasUnit",         "区域包含抽蓄机组"),
    comp(unit_zone, crane,    "zone",  "crane",           "zoneHasCrane",        "区域配备桥机"),
    # 抽蓄机组 → 各设备 (L2→L3)
    comp(ps_unit, pump_turbine, "unit", "turbine",        "unitHasTurbine",      "机组含水泵水轮机"),
    comp(ps_unit, gen_motor,    "unit", "genMotor",       "unitHasGenMotor",     "机组含发电电动机"),
    comp(ps_unit, inlet_valve,  "unit", "valve",          "unitHasValve",        "机组含进水球阀"),
    comp(ps_unit, governor,     "unit", "governor",       "unitHasGovernor",     "机组含调速器"),
    comp(ps_unit, exciter,      "unit", "exciter",        "unitHasExciter",      "机组含励磁系统"),
    # 其他分组 → 设备
    comp(intake_zone, intake_gate, "zone",  "gate",       "intakeHasGate",       "进水口含闸门"),
    comp(waterway, penstock,       "sys",   "penstock",   "waterwayHasPenstock", "输水含压力钢管"),
    comp(switchyard, main_transformer, "yard", "xfmr",    "yardHasXfmr",         "开关站含主变"),
    comp(switchyard, gis,          "yard",  "gis",        "yardHasGIS",          "开关站含GIS"),
    comp(aux_zone, aux_pump,       "zone",  "pump",       "auxHasPump",          "辅助区含辅机水泵"),
    # 水泵水轮机 → 部件 (L3→L4)
    comp(pump_turbine, runner,     "machine", "runner",   "ptHasRunner",         "水轮机含转轮"),
    comp(pump_turbine, main_shaft, "machine", "shaft",    "ptHasShaft",          "水轮机含主轴"),
    comp(pump_turbine, guide_bearing, "machine", "gb",    "ptHasGuideBearing",   "水轮机含导轴承"),
    comp(pump_turbine, thrust_bearing, "machine", "tb",   "ptHasThrustBearing",  "水轮机含推力轴承"),
    comp(pump_turbine, seal_ring,  "machine", "seal",     "ptHasSeal",           "水轮机含密封环"),
    # 发电电动机 → 部件
    comp(gen_motor, stator_coil,   "gm",    "stator",     "gmHasStator",         "电动机含定子线圈"),
    comp(gen_motor, rotor_pole,    "gm",    "rotor",      "gmHasRotor",          "电动机含转子磁极"),
    comp(gen_motor, cooler,        "gm",    "cooler",     "gmHasCooler",         "电动机含冷却器"),
    comp(gen_motor, guide_bearing, "gm",    "gb",         "gmHasGuideBearing",   "电动机含导轴承"),
    # 励磁系统 → 部件
    comp(exciter, exc_rectifier,   "exc",   "rect",       "excHasRect",          "励磁含整流柜"),
]


m1_classes = [
    plant,
    unit_zone, ps_unit, waterway, intake_zone, switchyard, aux_zone,
    pump_turbine, gen_motor, inlet_valve, governor, exciter,
    main_transformer, gis, crane, intake_gate, penstock, aux_pump,
    runner, main_shaft, guide_bearing, thrust_bearing,
    stator_coil, rotor_pole, cooler, exc_rectifier, seal_ring,
]

m1_pkg = Package(
    id=uid("m1/pkg"), name="PumpedStorageM1",
    label="抽水蓄能电站 M1 领域层",
    description="覆盖设施→功能分组(含嵌套)→设备→部件的完整抽水蓄能领域层。",
    classes=m1_classes,
    associations=m1_associations,
    structural_patterns=[],
    publish_status="draft",
)

m1_model = M1Model(
    id=M1_ID, name="PumpedStorageM1",
    label="抽水蓄能电站 · M1 (27 个领域类)",
    description="完整的抽蓄电站领域层,含电站/机组区域/机组/水轮机/发电电动机等类,"
                "并定义了它们之间的物理包含关系 (可在 M1 元结构树形面板中直观查看)。",
    m2_template_id=M2_ID,
    current_version="1.0",
    versions=[ModelVersion(
        version="1.0", created_at=datetime.utcnow().isoformat(),
        created_by="seed-script",
        changelog=f"初始版本: {len(m1_classes)} 个 M1 类 + {len(m1_associations)} 条 composition",
        package=m1_pkg,
    )],
    status="draft",
)


def main() -> None:
    import sys as _sys
    try: _sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    store = FileStore()
    store.save_model(m2_model)
    store.save_model(m1_model)
    print(f"[OK] Seeded M2 '{m2_model.id}': {len(m2_pkg.classes)} MetaClasses, "
          f"{len(m2_pkg.associations)} hierarchy assocs, "
          f"{len(m2_pkg.structural_patterns)} pattern, status={m2_model.status}")
    print(f"[OK] Seeded M1 '{m1_model.id}': {len(m1_classes)} classes, "
          f"{len(m1_associations)} compositions, status={m1_model.status}")
    # Level distribution
    lvls = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
    parent_to_level = {"Facility":"L1", "FunctionalGroup":"L2",
                       "Equipment":"L3", "Component":"L4"}
    for c in m1_classes:
        k = parent_to_level.get(c.parent_class_name)
        if k: lvls[k] += 1
    print(f"       level distribution: {lvls}")
    print("")
    print("前端 M1 视图刷新后,'M1 元结构面板' 将显示树形组合图:")
    print("  L1 抽蓄电站 -> L2 机组区域/输水/进水口/开关站/辅助区")
    print("             L2 机组区域 -> L2 抽蓄机组 (同层嵌套)")
    print("                            -> L3 水泵水轮机/发电电动机/球阀/调速器/励磁")
    print("                                 -> L4 转轮/主轴/导轴承/定子线圈/整流柜...")


if __name__ == "__main__":
    main()
