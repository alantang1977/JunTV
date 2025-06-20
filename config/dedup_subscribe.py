import os
from pathlib import Path

def deduplicate_subscribe():
    # 定义源文件和输出文件的路径
    source_file = Path("config/subscribe.txt")
    output_dir = Path("config/deduped")
    output_file = output_dir / "subscribe.txt"

    # 调试输出：显示源文件路径
    print(f"源文件路径: {source_file.resolve()}")
    print(f"输出文件路径: {output_file.resolve()}")

    # 检查源文件是否存在
    if not source_file.exists():
        print(f"错误：未找到源文件 {source_file}")
        return

    # 读取源文件内容
    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"错误：读取文件时发生异常: {e}")
        return

    # 去重处理
    url_set = set()
    deduped_lines = []
    for line in lines:
        line = line.rstrip('\n')  # 去除行尾换行符
        if line.startswith(('http://', 'https://')):
            if line not in url_set:
                url_set.add(line)
                deduped_lines.append(line)
        else:
            deduped_lines.append(line)

    # 创建输出目录（如果不存在）
    output_dir.mkdir(parents=True, exist_ok=True)

    # 写入去重后的内容到输出文件
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            for line in deduped_lines:
                f.write(line + '\n')
    except Exception as e:
        print(f"错误：写入文件时发生异常: {e}")
        return

    # 输出处理结果
    print(f"处理完成！")
    print(f"写入行数: {len(deduped_lines)}")
    print(f"输出文件路径: {output_file.resolve()}")

if __name__ == "__main__":
    deduplicate_subscribe()    
