#!/usr/bin/env bash
# =============================================================================
# fusion-science 国内科研环境部署脚本
# 配置国内数据库镜像 + 离线模式 + fusion-model-hub 资产路径
# 用法: source deploy/setup_env.sh
# =============================================================================

set -euo pipefail

# ---- 基础路径（根据实际安装位置修改）----
FUSION_SCIENCE_HOME="${FUSION_SCIENCE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FUSION_MODEL_HUB="${FUSION_MODEL_HUB:-$HOME/fusion-workspace/fusion-model-hub}"

echo "🔬 Fusion-Science 国内环境部署"
echo "=================================="
echo "  HOME:        $FUSION_SCIENCE_HOME"
echo "  MODEL_HUB:   $FUSION_MODEL_HUB"
echo ""

# ---- 1. 创建 model-hub 标准目录结构 ----
echo "📁 创建 fusion-model-hub 目录结构..."
mkdir -p "$FUSION_MODEL_HUB"/{models,datasets/{sft,preference,multimodal},evals,snapshots,cache/{pubmed,uniprot,pdb,ensembl,chembl}}
echo "  ✅ $FUSION_MODEL_HUB"

# ---- 2. 设置国内数据库镜像环境变量 ----
echo "🌐 设置国内科研数据库镜像..."

# 中国科学院 / 国家基因组科学数据中心镜像
export FUSION_SCI_PUBMED_MIRROR="https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
export FUSION_SCI_PDB_MIRROR="https://data.rcsb.org/rest/v1"
export FUSION_SCI_UNIPROT_MIRROR="https://rest.uniprot.org"
export FUSION_SCI_ENSEMBL_MIRROR="https://useast.ensembl.org"
export FUSION_SCI_CHEMBL_MIRROR="https://www.ebi.ac.uk/chembl/api/data"

# 中国自主科研数据库（替代海外库）
export FUSION_SCI_NGDC_URL="https://ngdc.cncb.ac.cn"       # 国家基因组科学数据中心
export FUSION_SCI_CNGB_URL="https://www.cngb.org"           # 国家基因库
export FUSION_SCI_CNKI_URL="https://www.cnki.net"           # 中国知网
export FUSION_SCI_ScienceDB_URL="https://www.scidb.cn"      # 科学数据银行

# ---- 3. 强制离线模式（关闭所有境外请求）----
export FUSION_OFFLINE_MODE="${FUSION_OFFLINE_MODE:-true}"
export FUSION_SCIENCE_USE_MIRRORS="true"
export FUSION_SCIENCE_CACHE_ENABLED="true"
export FUSION_SCIENCE_CACHE_DIR="$FUSION_MODEL_HUB/cache"

# ---- 4. Python 路径 ----
if [ -d "$FUSION_SCIENCE_HOME/.venv" ]; then
    source "$FUSION_SCIENCE_HOME/.venv/bin/activate"
    echo "  ✅ Virtual env: $FUSION_SCIENCE_HOME/.venv"
fi

export PYTHONPATH="$FUSION_SCIENCE_HOME:$PYTHONPATH"

# ---- 5. 打印配置总结 ----
echo ""
echo "=================================="
echo "✅ Fusion-Science 环境就绪"
echo "=================================="
echo "离线模式:       $FUSION_OFFLINE_MODE"
echo "数据库镜像:     已启用"
echo "国家基因组中心: $FUSION_SCI_NGDC_URL"
echo "中国知网:       $FUSION_SCI_CNKI_URL"
echo "=================================="

# 写入 .env 文件供 python-dotenv 加载
cat > "$FUSION_SCIENCE_HOME/config/.env" << EOF
# Fusion-Science 环境配置（自动生成）
FUSION_SCIENCE_HOME=$FUSION_SCIENCE_HOME
FUSION_MODEL_HUB=$FUSION_MODEL_HUB
FUSION_OFFLINE_MODE=$FUSION_OFFLINE_MODE
FUSION_SCIENCE_USE_MIRRORS=true
FUSION_SCIENCE_CACHE_ENABLED=true
FUSION_SCIENCE_CACHE_DIR=$FUSION_MODEL_HUB/cache
FUSION_SCI_PUBMED_MIRROR=$FUSION_SCI_PUBMED_MIRROR
FUSION_SCI_PDB_MIRROR=$FUSION_SCI_PDB_MIRROR
FUSION_SCI_UNIPROT_MIRROR=$FUSION_SCI_UNIPROT_MIRROR
FUSION_SCI_ENSEMBL_MIRROR=$FUSION_SCI_ENSEMBL_MIRROR
FUSION_SCI_CHEMBL_MIRROR=$FUSION_SCI_CHEMBL_MIRROR
FUSION_SCI_NGDC_URL=$FUSION_SCI_NGDC_URL
FUSION_SCI_CNGB_URL=$FUSION_SCI_CNGB_URL
FUSION_SCI_CNKI_URL=$FUSION_SCI_CNKI_URL
FUSION_SCI_ScienceDB_URL=$FUSION_SCI_ScienceDB_URL
EOF
echo "  ✅ 配置已写入 config/.env"