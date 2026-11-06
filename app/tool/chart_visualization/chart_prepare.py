from app.tool.chart_visualization.python_execute import NormalPythonExecute


class VisualizationPrepare(NormalPythonExecute):
    """A tool for Chart Generation Preparation"""

    name: str = "visualization_preparation"
    description: str = "用 Python 代码生成 data_visualization 工具所需的元数据。输出：1）JSON 信息。2）清洗后的 CSV 数据文件（可选）。"
    parameters: dict = {
        "type": "object",
        "properties": {
            "code_type": {
                "description": "代码类型。visualization：csv -> 图表；insight：将洞察加入图表",
                "type": "string",
                "default": "visualization",
                "enum": ["visualization", "insight"],
            },
            "code": {
                "type": "string",
                "description": """用于 data_visualization 准备的 Python 代码。
## Visualization 类型
1. 数据加载逻辑
2. 生成 CSV 数据和图表描述
2.1 CSV 数据（要可视化的数据，从原始数据清洗 / 转换而来，保存为 .csv）
2.2 CSV 数据的图表描述（标题或描述应简洁明确。例如：「产品销量分布」「月度收入趋势」。）
3. 将信息保存为 json 文件（格式：{"csvFilePath": string, "chartTitle": string}[]）
## Insight 类型
1. 从 data_visualization 结果中选择要加入图表的洞察。
2. 将信息保存为 json 文件（格式：{"chartPath": string, "insights_id": number[]}[]）
# 注意
1. 可以根据不同可视化需求生成一份或多份 CSV 数据。
2. 每份图表数据应简单、干净且彼此不同。
3. Json 文件以 utf-8 保存，并打印路径：print(json_path)
""",
            },
        },
        "required": ["code", "code_type"],
    }
