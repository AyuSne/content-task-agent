# 创作提交与返修示例

日期：2026-10-29（北京时间）。状态：讨论草案，无运行代码或真实模型请求。Q1至Q28的结论以[设计改进记录](design-improvements.md)为准，Q26、Q27已确认接收语义，Q28已确认整体职责关系；本文中的精确字段和示例费用尚未整体确认。

本文用于核对[模型输入草案](model-input-draft.md)中的创作提交interface，不重复整份原生工具定义或Chat消息包装。以下任务、材料、ID、版本与金额均为示意，不是现有任务记录、真实产品能力或已定新价目表。实际接入须将确定后的字段写入原生工具定义，不能只让它们存在于本文。

## 调用前模型已经知道什么

- 常驻系统正文说明创作责任、资源口径、版本及完成条件。
- 创作Skill正文已明确预装；条件性引用通过read_skill按需取得。
- 原生工具定义说明submit_script等能力的用途、参数及必要前提。
- 宿主已提供任务输入、材料目录与有关正文、适用动作费用概览及当前状态。
- 当前任务归属、调用ID和适用费用表由宿主管理，模型不在创作参数中重填这些配置。

本例的任务输入要点：

```json
{
  "input_version": 1,
  "topic": "介绍帧剪的字幕操作流程",
  "audience": "首次接触字幕编辑的用户",
  "requirements": [
    "主视频15秒，展示字幕操作步骤。",
    "产品主张必须有材料支持。"
  ]
}
```

模型已取得的示例材料M-DEMO-001、版本materials-demo-v1包含原文：“帧剪支持将视频人声识别为字幕，并允许逐句编辑字幕。”其范围仅支持这两项功能，不表示识别始终准确、制作素材已存在或推广效果已获保证。

## 首次脚本提交，可以同时携带简报

下面是模型输出的submit_script参数示例。brief与brief_ref二选一是本例提出的字段组织方式，尚未定为最终schema；单独提交过适用简报时使用引用。

```json
{
  "brief": {
    "objective": "让新用户理解识别字幕和逐句编辑的操作流程",
    "audience": "首次接触字幕编辑的用户",
    "confirmed_facts": [
      {
        "claim": "支持识别人声字幕及逐句编辑",
        "material_id": "M-DEMO-001",
        "material_version": "materials-demo-v1",
        "quote": "帧剪支持将视频人声识别为字幕，并允许逐句编辑字幕。",
        "scope": "仅支持字幕识别及编辑能力"
      }
    ],
    "requirements": [
      {
        "input_version": 1,
        "field_path": "requirements[0]",
        "quote": "主视频15秒，展示字幕操作步骤。"
      },
      {
        "input_version": 1,
        "field_path": "requirements[1]",
        "quote": "产品主张必须有材料支持。"
      }
    ],
    "suggestions": ["采用实际操作演示表达功能"],
    "assumptions": [],
    "open_questions": [
      {
        "question": "操作演示录屏尚待制作，具体画面需在制作时核对",
        "blocking": false
      }
    ]
  },
  "script": {
    "title": "字幕识别后，逐句改字",
    "duration_seconds": 15,
    "segments": [
      {
        "segment_id": "segment-1",
        "duration_seconds": 5,
        "visual": "待制作录屏：演示将视频人声识别为字幕，具体操作画面需核对",
        "voiceover": "先识别人声字幕，再逐句检查。",
        "on_screen_text": "识别字幕",
        "evidence_ids": ["E-DEMO-1"]
      },
      {
        "segment_id": "segment-2",
        "duration_seconds": 5,
        "visual": "待制作录屏：演示逐句编辑字幕",
        "voiceover": "检查字幕内容，逐句改字。",
        "on_screen_text": "逐句编辑",
        "evidence_ids": ["E-DEMO-1"]
      },
      {
        "segment_id": "segment-3",
        "duration_seconds": 5,
        "visual": "待制作流程示意：回顾识别、检查和修改字幕",
        "voiceover": "识别后记得检查，再完成这条视频。",
        "on_screen_text": "识别 → 检查 → 修改",
        "evidence_ids": []
      }
    ],
    "evidence": [
      {
        "id": "E-DEMO-1",
        "claim": "支持识别人声字幕及逐句编辑",
        "material_id": "M-DEMO-001",
        "material_version": "materials-demo-v1",
        "quote": "帧剪支持将视频人声识别为字幕，并允许逐句编辑字幕。",
        "scope": "仅支持字幕识别及编辑能力",
        "use": "支撑前两段的功能表达"
      }
    ]
  }
}
```

模型已经在参数中完成语义创作。程序负责核对结构、引用、版本及资源，按已确认规则登记业务尝试与计量，保存接收的产物并返回事实。执行前拒绝不收模拟生成费；业务启动后的失败或中断按D-07保留消耗，同次尝试恢复不重复计量。

两部分均成功接收时，工具返回可以采用如下回执。这里展示的是实际tool消息中的业务结果草案，不是请求级output_schema：

```json
{
  "status": "stored",
  "brief_ref": {"id": "B-DEMO-1", "version": 1},
  "script_ref": {"id": "S-DEMO-1", "version": 1},
  "attempt": {"id": "A-DEMO-1", "started": true},
  "consumption": {
    "pricing_version": "pricing-demo-v1",
    "simulated_cost": {"amount": 20, "unit": "CNY"},
    "simulated_duration_seconds": 60
  }
}
```

这说明简报和脚本版本已接收，不表示完整活动方案已完成、核验已通过或交付文件已核实保存。回执不重复输出刚提交的脚本。实际运行还需提供必要的最新资源事实，本例不假设它们已由示例金额核算完成。

## 返修提交完整脚本，复用适用简报

假设完整方案CAM-DEMO-1/v1引用S-DEMO-1/v1，核验反馈开场与第二段表达重复。主Agent决定压缩开场，后两段保持原内容。返修仍提交完整脚本，引用旧版本及仍适用的简报：

```json
{
  "brief_ref": {"id": "B-DEMO-1", "version": 1},
  "revises_script_ref": {"id": "S-DEMO-1", "version": 1},
  "script": {
    "title": "字幕识别后，逐句改字",
    "duration_seconds": 15,
    "segments": [
      {
        "segment_id": "segment-1",
        "duration_seconds": 5,
        "visual": "待制作录屏：演示将视频人声识别为字幕，具体操作画面需核对",
        "voiceover": "字幕先识别，内容自己改。",
        "on_screen_text": "识别字幕",
        "evidence_ids": ["E-DEMO-1"]
      },
      {
        "segment_id": "segment-2",
        "duration_seconds": 5,
        "visual": "待制作录屏：演示逐句编辑字幕",
        "voiceover": "检查字幕内容，逐句改字。",
        "on_screen_text": "逐句编辑",
        "evidence_ids": ["E-DEMO-1"]
      },
      {
        "segment_id": "segment-3",
        "duration_seconds": 5,
        "visual": "待制作流程示意：回顾识别、检查和修改字幕",
        "voiceover": "识别后记得检查，再完成这条视频。",
        "on_screen_text": "识别 → 检查 → 修改",
        "evidence_ids": []
      }
    ],
    "evidence": [
      {
        "id": "E-DEMO-1",
        "claim": "支持识别人声字幕及逐句编辑",
        "material_id": "M-DEMO-001",
        "material_version": "materials-demo-v1",
        "quote": "帧剪支持将视频人声识别为字幕，并允许逐句编辑字幕。",
        "scope": "仅支持字幕识别及编辑能力",
        "use": "支撑前两段的功能表达"
      }
    ]
  }
}
```

程序成功接收后返回新脚本引用，例如S-DEMO-2/v2。旧方案CAM-DEMO-1/v1仍引用旧脚本，主Agent需提交引用新脚本的完整方案快照，例如CAM-DEMO-2/v2，再针对该完整版本核验和处置，满足条件后核实保存。这个关系解释版本责任，不要求每次返修机械重走全部创作步骤。

## 已确认的失败接收规则

| 情景 | 已确认的选择 | 模型能够据此判断的事实 |
| --- | --- | --- |
| 联合提交中简报有效、脚本参数无效 | Q26：执行前整项校验，全部有效才启动业务尝试 | 校验失败不形成其中任一新产物版本；实际调用和具体错误保留，主Agent修正后提交；成功时一并保存版本 |
| 批量处置中第一项有效、第二项引用不存在的requirements[8] | Q27：整批先校验，全部有效才记录 | 返回无效项的位置和原因，原处置状态保持，主Agent修正后重新提交 |

用户已以“OK”确认上述两项接收语义，尚未实施。它们不改变既有计量、未知结果核实或中断恢复要求；启动后的失败和消耗如实记录，实现中的可恢复事实仍需核实，不能用预期状态掩盖实际副作用或未知。
