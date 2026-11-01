# -*- coding: utf-8 -*-
"""发布链定位选择器注册表（P0-a 第一刀外置，2026-11-01）。

背景（迭代计划 P0-a，复核实测）：发布包共 113 处 ``querySelector`` 硬编码
调用点，其中 **68 处是定位型真靶点**（每个逗号分支都依赖类/属性/伪类——
平台改版 CSS/DOM 即全灭），45 处是遍历型（有裸标签分支，文本匹配仍能兜住，
改版不失效）。真靶点过去散在执行器的 JS 字符串里，改版时要改 Python 源码；
本模块把它们收拢成**单一真源**：

- 执行器 JS 一律 ``"%s"`` 内插本表常量——字面量不再出现在调用点（守测
  ``tests/test_publish_selectors_registry.py`` 把关：共用层定位型 LIT = 0）；
- ``EXPECTED_PRE_MIGRATION`` 是迁移前字面量的**逐字节快照**，守测用它锁
  等价性：改这里 = 改平台行为，必须显式过评审并同步校准表；
- 后续刀（番茄 56 / 七猫 13 与平台适配层）按同一模式迁移，最终目标
  LIT 总数 ≤30（P0-a 验收①）。

命名约定：键 = 语义角色（不是选择器本身），换平台=换值不改代码。
"""
from __future__ import annotations

# 语义角色 → 选择器（值 = 迁移前执行器字面量，逐字节一致）
DEFAULTS = {
    # 单选（HTML 标准语义；番茄/七猫的受控组件都保留原生 input）
    "radio_input": "input[type=radio]",
    # 单选/复选选中态回读（checked_js 闭环）
    "radio_checkbox_input": "input[type=radio],input[type=checkbox]",
    # radio 分组容器（scope_text 定位表单行的候选面）
    "form_row": "[class*=form-item],[class*=form_row],fieldset",
    # Element UI 表单行（fill_label：label 文本定位控件）
    "form_item": ".el-form-item,[class*=form-item]",
    # 表单行内 label 文本节点（历史字面量即重复两遍，逐字节保留）
    "label_part": "[class*=label],[class*=label]",
    # 番茄「有刚刚更新的草稿」恢复弹层（stale draft modal）
    "stale_modal": "[role=dialog],.arco-modal,.byte-modal-wrapper",
    # 弹层枚举全集（js_click 的 gm 分支：提交前的阻塞弹层清障）
    "dialog_enum": ".el-dialog,.modal,[role=dialog],.arco-modal,.byte-modal-wrapper",
    # 弹层范围（click_match：在弹层内找关键词，无弹层落回整页）
    "dialog_scope": ".el-dialog,.modal,[role=dialog]",
    # arco 复选框的可点面（checkbox 步真实鼠标路径）
    "check_mask": ".arco-checkbox-mask",
    # 番茄正文编辑器根（title 块定位）
    "contenteditable": ".q-contenteditable",
    # 七猫标签弹层：分组容器 / 组名 / 选中芯片 / 芯片（tags 步闭环）
    "tag_wrap": ".tags-wrap",
    "tag_title": ".tags-tit",
    "tag_selected_chip": ".tags-box .tags-con.tag-selected .tags-con-name",
    "tag_chip": ".tags-box .tags-con-name",

    # ---- 番茄平台层（arco design；flow_fanqie.py + fanqie.py，第二刀）----
    # 弹层范围（提交链各步的阻塞弹层探测，全文件高频复用）
    "fq_dialog": "[role=dialog],.arco-modal",
    # 开关（定时/声明等 role=switch 受控件，三种历史形态逐字节保留）
    "fq_switch": "button[role=switch],input[type=checkbox],.arco-switch",
    "fq_switch_bare": "button[role=switch]",
    "fq_switch_alt": "button[role=switch],.arco-switch",
    # 提交反馈面（toast/通知/弹层通知）
    "fq_alert": "[role=alert],.arco-message,.arco-notification,.arco-modal-notice",
    # 主操作按钮（立即发布/确定）
    "fq_btn_primary": "button.arco-btn-primary",
    # 定时发布：日期/时间输入与 arco picker 部件
    "fq_date_input": "input[placeholder=\"请选择日期\"]",
    "fq_time_input": "input[placeholder=\"请选择时间\"]",
    "fq_picker_cell": ".arco-picker-cell-in-view",
    "fq_picker_nav": ".arco-picker-header-icon",
    "fq_picker_value": ".arco-picker-header-value",
    "fq_timepicker": ".arco-timepicker",
    "fq_timepicker_list": ".arco-timepicker-list",
    "fq_timepicker_cell": ".arco-timepicker-cell",
    "fq_timepicker_container": ".arco-timepicker-container",
    # 章节管理翻页与加载态（对账翻页）
    "fq_pagination_next": ".arco-pagination-item-next",
    "fq_spin": ".arco-spin,[aria-busy=true]",
    # 短篇链：章节选择下拉与付费按钮
    "fq_chapter_select": ".chapter-select-left .byte-select-view",
    "fq_select_popup": ".byte-select-popup,[class*=select-dropdown]",
    "fq_pay_button": ".publish-short-config-pay button",

    # ---- 七猫平台层（Element UI；flow_qimao.py + qimao.py，第三刀）----
    "qm_el_input": ".el-input__inner",
    "qm_el_select": ".el-select",
    "qm_el_option": ".el-select-dropdown__item",
    "qm_table_row": ".el-table__row",
    "qm_loading": ".el-loading-mask,[aria-busy=true]",

    # ---- 遍历型 scope（收官④：多平台复用的候选枚举面，语义化入册）----
    # 这些是纯标签组合（平台改版不失效），入册目的：跨平台口径统一 +
    # LIT 棘轮终态（全包 querySelector 字面量调用点 ≤30）。
    "scope_broad": "a,button,[role=button],span,li,div,section",
    "scope_clickable": "button,a,[role=button],span",
    "fq_option_item": "li,[class*=option]",
    "scope_btn_role_a": "button,[role=button],a",
    "scope_btn_broad": "a,button,[role=button],[class*=btn],span",
    "scope_btn_role": "a,button,[role=button]",
    "qm_btn_scope": "button,a,[role=button]",
    "bare_button": "button",
    "any_el": "*",
    "body_any": "body *",
    "click_text_scope": "button,a,[role=button],span,li",
}

# 迁移前字面量快照（等价锁；与 DEFAULTS 逐键一致由守测强制）
EXPECTED_PRE_MIGRATION = {
    "radio_input": "input[type=radio]",
    "radio_checkbox_input": "input[type=radio],input[type=checkbox]",
    "form_row": "[class*=form-item],[class*=form_row],fieldset",
    "form_item": ".el-form-item,[class*=form-item]",
    "label_part": "[class*=label],[class*=label]",
    "stale_modal": "[role=dialog],.arco-modal,.byte-modal-wrapper",
    "dialog_enum": ".el-dialog,.modal,[role=dialog],.arco-modal,.byte-modal-wrapper",
    "dialog_scope": ".el-dialog,.modal,[role=dialog]",
    "check_mask": ".arco-checkbox-mask",
    "contenteditable": ".q-contenteditable",
    "tag_wrap": ".tags-wrap",
    "tag_title": ".tags-tit",
    "tag_selected_chip": ".tags-box .tags-con.tag-selected .tags-con-name",
    "tag_chip": ".tags-box .tags-con-name",
    "fq_dialog": "[role=dialog],.arco-modal",
    "fq_switch": "button[role=switch],input[type=checkbox],.arco-switch",
    "fq_switch_bare": "button[role=switch]",
    "fq_switch_alt": "button[role=switch],.arco-switch",
    "fq_alert": "[role=alert],.arco-message,.arco-notification,.arco-modal-notice",
    "fq_btn_primary": "button.arco-btn-primary",
    "fq_date_input": "input[placeholder=\"请选择日期\"]",
    "fq_time_input": "input[placeholder=\"请选择时间\"]",
    "fq_picker_cell": ".arco-picker-cell-in-view",
    "fq_picker_nav": ".arco-picker-header-icon",
    "fq_picker_value": ".arco-picker-header-value",
    "fq_timepicker": ".arco-timepicker",
    "fq_timepicker_list": ".arco-timepicker-list",
    "fq_timepicker_cell": ".arco-timepicker-cell",
    "fq_timepicker_container": ".arco-timepicker-container",
    "fq_pagination_next": ".arco-pagination-item-next",
    "fq_spin": ".arco-spin,[aria-busy=true]",
    "fq_chapter_select": ".chapter-select-left .byte-select-view",
    "fq_select_popup": ".byte-select-popup,[class*=select-dropdown]",
    "fq_pay_button": ".publish-short-config-pay button",
    "qm_el_input": ".el-input__inner",
    "qm_el_select": ".el-select",
    "qm_el_option": ".el-select-dropdown__item",
    "qm_table_row": ".el-table__row",
    "qm_loading": ".el-loading-mask,[aria-busy=true]",
    "scope_broad": "a,button,[role=button],span,li,div,section",
    "scope_clickable": "button,a,[role=button],span",
    "fq_option_item": "li,[class*=option]",
    "scope_btn_role_a": "button,[role=button],a",
    "scope_btn_broad": "a,button,[role=button],[class*=btn],span",
    "scope_btn_role": "a,button,[role=button]",
    "qm_btn_scope": "button,a,[role=button]",
    "bare_button": "button",
    "any_el": "*",
    "body_any": "body *",
    "click_text_scope": "button,a,[role=button],span,li",
}


def get(name):
    """取选择器；未知角色直接 KeyError——新增角色必须先登记（防裸字面量回流）。"""
    return DEFAULTS[name]
