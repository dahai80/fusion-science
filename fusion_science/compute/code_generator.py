from __future__ import annotations

import logging
from dataclasses import dataclass, field

from fusion_science.core.gateway import LLMGateway

logger = logging.getLogger(__name__)


@dataclass
class CodeSuggestion:
    code: str = ""
    language: str = "python"
    description: str = ""
    packages_needed: list[str] = field(default_factory=list)
    estimated_runtime: str = "unknown"
    confidence: float = 0.0


_BIOINFORMATICS_TEMPLATES: dict[str, dict] = {
    "deseq2": {
        "keywords": ["deseq2", "differential expression", "diff expression", "differential gene", "deg", "rna-seq", "rnaseq", "deseq"],
        "language": "R",
        "description": "Differential expression analysis using DESeq2",
        "packages": ["DESeq2", "apeglm"],
        "estimated_runtime": "5-30 min",
        "confidence": 0.9,
        "code": """\
library(DESeq2)
library(apeglm)

# Load count matrix and sample metadata
# count_matrix: rows=gene_id, columns=samples
# col_data: DataFrame with condition column
dds <- DESeqDataSetFromMatrix(
    countData = count_matrix,
    colData   = col_data,
    design    = ~ condition
)

# Pre-filter low-count genes
keep <- rowSums(counts(dds) >= 10) >= 3
dds <- dds[keep, ]

# Run DESeq2 pipeline
dds <- DESeq(dds)

# Results: condition treated vs control
res <- results(dds, contrast = c("condition", "treated", "control"))
res <- lfcShrink(dds, coef = "condition_treated_vs_control", type = "apeglm")

# Summary
summary(res)

# Export significant genes
sig <- subset(res, padj < 0.05 & abs(log2FoldChange) > 1)
write.csv(as.data.frame(sig), "deseq2_results.csv")
""",
    },
    "go_enrichment": {
        "keywords": ["go enrichment", "gene ontology", "go analysis", "enrichment analysis", "functional enrichment", "pathway enrichment"],
        "language": "R",
        "description": "GO enrichment analysis using clusterProfiler",
        "packages": ["clusterProfiler", "org.Hs.eg.db", "enrichplot"],
        "estimated_runtime": "2-10 min",
        "confidence": 0.85,
        "code": """\
library(clusterProfiler)
library(org.Hs.eg.db)
library(enrichplot)

# gene_list: named vector of log2FoldChange, sorted decreasing
gene_list <- sort(gene_list, decreasing = TRUE)

# GO over-representation analysis
go_ego <- enrichGO(
    gene          = names(gene_list)[gene_list > 1],
    OrgDb         = org.Hs.eg.db,
    keyType       = "ENTREZID",
    ont           = "BP",
    pAdjustMethod = "BH",
    pvalueCutoff  = 0.05,
    qvalueCutoff  = 0.2,
)

# Dot plot
dotplot(go_ego, showCategory = 20) + ggtitle("GO Biological Process Enrichment")

# KEGG pathway enrichment
kegg_res <- enrichKEGG(
    gene         = names(gene_list)[gene_list > 1],
    organism     = "hsa",
    pvalueCutoff = 0.05,
)

write.csv(as.data.frame(go_ego), "go_enrichment_results.csv")
""",
    },
    "correlation": {
        "keywords": ["correlation", "pearson", "spearman", "correlate", "correlation analysis", "correlation matrix"],
        "language": "python",
        "description": "Correlation analysis with heatmap visualization",
        "packages": ["pandas", "scipy", "matplotlib", "seaborn"],
        "estimated_runtime": "< 1 min",
        "confidence": 0.9,
        "code": """\
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats

# df: DataFrame with numeric columns
corr_matrix = df.corr(method='pearson')
print(corr_matrix)

# p-value matrix
pval_matrix = pd.DataFrame(np.zeros_like(corr_matrix), columns=corr_matrix.columns, index=corr_matrix.index)
for col1 in df.select_dtypes(include=[np.number]).columns:
    for col2 in df.select_dtypes(include=[np.number]).columns:
        _, pval = stats.pearsonr(df[col1].dropna(), df[col2].dropna())
        pval_matrix.loc[col1, col2] = pval

# Heatmap
fig, ax = plt.subplots(figsize=(10, 8))
mask = np.triu(np.ones_like(corr_matrix, dtype=bool))
sns.heatmap(corr_matrix, mask=mask, annot=True, fmt='.2f', cmap='RdBu_r',
            center=0, square=True, ax=ax)
ax.set_title('Pearson Correlation Matrix')
plt.tight_layout()
plt.savefig('correlation_heatmap.png', dpi=150)
plt.show()
""",
    },
    "ttest": {
        "keywords": ["t-test", "ttest", "t test", "student", "welch", "two-sample", "two sample", "hypothesis test", "means comparison"],
        "language": "python",
        "description": "Two-sample t-test with effect size",
        "packages": ["scipy", "numpy", "pandas"],
        "estimated_runtime": "< 1 min",
        "confidence": 0.9,
        "code": """\
import numpy as np
import pandas as pd
from scipy import stats

# group_a, group_b: arrays or Series of numeric values
group_a = np.array(group_a)
group_b = np.array(group_b)

# Welch's t-test (does not assume equal variance)
t_stat, p_value = stats.ttest_ind(group_a, group_b, equal_var=False)

# Effect size (Cohen's d)
pooled_std = np.sqrt(
    ((len(group_a) - 1) * np.var(group_a, ddof=1) + (len(group_b) - 1) * np.var(group_b, ddof=1))
    / (len(group_a) + len(group_b) - 2)
)
cohens_d = (np.mean(group_a) - np.mean(group_b)) / pooled_std

print(f"Welch's t-test: t = {t_stat:.4f}, p = {p_value:.6f}")
print(f"Cohen's d = {cohens_d:.4f}")
print(f"Group A: mean={np.mean(group_a):.4f}, std={np.std(group_a, ddof=1):.4f}, n={len(group_a)}")
print(f"Group B: mean={np.mean(group_b):.4f}, std={np.std(group_b, ddof=1):.4f}, n={len(group_b)}")
print(f"Significant at alpha=0.05: {p_value < 0.05}")
""",
    },
    "pca": {
        "keywords": ["pca", "principal component", "dimensionality reduction", "principal component analysis"],
        "language": "python",
        "description": "Principal Component Analysis with visualization",
        "packages": ["pandas", "numpy", "scipy", "matplotlib", "sklearn"],
        "estimated_runtime": "1-5 min",
        "confidence": 0.9,
        "code": """\
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA

# df: DataFrame with numeric features, optionally a 'label' column for coloring
features = df.select_dtypes(include=[np.number])
labels = df['label'] if 'label' in df.columns else None

# Standardize
scaler = StandardScaler()
X_scaled = scaler.fit_transform(features)

# PCA
pca = PCA()
X_pca = pca.fit_transform(X_scaled)

# Explained variance
explained = pca.explained_variance_ratio_
print(f"PC1: {explained[0]*100:.1f}%, PC2: {explained[1]*100:.1f}%")
print(f"Cumulative (top 5): {np.cumsum(explained[:5])*100}")

# Scatter plot PC1 vs PC2
fig, ax = plt.subplots(figsize=(8, 6))
scatter = ax.scatter(X_pca[:, 0], X_pca[:, 1],
                     c=labels if labels is not None else 'steelblue',
                     cmap='Set1', alpha=0.7, edgecolors='k', linewidth=0.5)
ax.set_xlabel(f'PC1 ({explained[0]*100:.1f}%)')
ax.set_ylabel(f'PC2 ({explained[1]*100:.1f}%)')
ax.set_title('PCA Score Plot')
if labels is not None:
    plt.colorbar(scatter, label='Label')
plt.tight_layout()
plt.savefig('pca_score_plot.png', dpi=150)
plt.show()
""",
    },
    "clustering": {
        "keywords": ["clustering", "kmeans", "k-means", "hierarchical", "cluster analysis", "unsupervised"],
        "language": "python",
        "description": "K-means clustering with elbow method and silhouette",
        "packages": ["pandas", "numpy", "matplotlib", "sklearn", "seaborn"],
        "estimated_runtime": "1-10 min",
        "confidence": 0.85,
        "code": """\
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

# df: DataFrame with numeric features
features = df.select_dtypes(include=[np.number])
scaler = StandardScaler()
X_scaled = scaler.fit_transform(features)

# Elbow method
K_range = range(2, 11)
inertias = []
silhouettes = []
for k in K_range:
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    km.fit(X_scaled)
    inertias.append(km.inertia_)
    silhouettes.append(silhouette_score(X_scaled, km.labels_))

# Plot elbow + silhouette
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
ax1.plot(K_range, inertias, 'bo-')
ax1.set_xlabel('k'); ax1.set_ylabel('Inertia'); ax1.set_title('Elbow Method')
ax2.plot(K_range, silhouettes, 'ro-')
ax2.set_xlabel('k'); ax2.set_ylabel('Silhouette'); ax2.set_title('Silhouette Score')
plt.tight_layout()
plt.savefig('clustering_elbow.png', dpi=150)
plt.show()

# Final clustering with best k
best_k = list(K_range)[np.argmax(silhouettes)]
km_final = KMeans(n_clusters=best_k, n_init=10, random_state=42)
df['cluster'] = km_final.fit_predict(X_scaled)
print(f"Best k={best_k}, silhouette={max(silhouettes):.4f}")
print(df['cluster'].value_counts().sort_index())
""",
    },
}

_CODE_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "Generated analysis code",
        },
        "description": {
            "type": "string",
            "description": "Brief description of what the code does",
        },
        "packages_needed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of packages/libraries required",
        },
        "estimated_runtime": {
            "type": "string",
            "description": "Estimated runtime (e.g. '< 1 min', '5-30 min')",
        },
    },
    "required": ["code", "description", "packages_needed"],
}


def _match_template(query: str, language: str) -> dict | None:
    query_lower = query.lower()
    best_match = None
    best_score = 0
    for _name, tpl in _BIOINFORMATICS_TEMPLATES.items():
        if language != "auto" and tpl["language"].lower() != language.lower():
            continue
        score = sum(1 for kw in tpl["keywords"] if kw in query_lower)
        if score > best_score:
            best_score = score
            best_match = tpl
    return best_match


class CodeGenerator:

    def __init__(self, gateway: LLMGateway | None = None):
        self._gateway = gateway
        logger.info("CodeGenerator initialized, gateway=%s", "provided" if gateway else "none (rule-based only)")

    async def generate(
        self,
        query: str,
        context: str = "",
        language: str = "python",
    ) -> CodeSuggestion:
        logger.info("generate query=%r language=%s", query[:80], language)

        if self._gateway is not None:
            try:
                result = await self._generate_via_gateway(query, context, language)
                if result is not None:
                    return result
                logger.warning("Gateway generation returned None, falling back to rule-based")
            except Exception as e:
                logger.error("Gateway generation failed: %s", e, exc_info=True)

        return self._rule_based_generate(query, language)

    async def generate_batch(
        self,
        queries: list[str],
        language: str = "python",
    ) -> list[CodeSuggestion]:
        logger.info("generate_batch count=%d language=%s", len(queries), language)
        results = []
        for q in queries:
            suggestion = await self.generate(q, language=language)
            results.append(suggestion)
        return results

    def _rule_based_generate(self, query: str, language: str) -> CodeSuggestion:
        logger.debug("rule_based_generate query=%r language=%s", query[:80], language)
        tpl = _match_template(query, language)
        if tpl is not None:
            logger.info("Matched template: %s", tpl["description"])
            return CodeSuggestion(
                code=tpl["code"],
                language=tpl["language"].lower(),
                description=tpl["description"],
                packages_needed=list(tpl["packages"]),
                estimated_runtime=tpl["estimated_runtime"],
                confidence=tpl["confidence"],
            )

        fallback = self._build_fallback(query, language)
        logger.info("No template match, using generic fallback for query=%r", query[:80])
        return fallback

    async def _generate_via_gateway(
        self,
        query: str,
        context: str,
        language: str,
    ) -> CodeSuggestion | None:
        messages = self._gateway.build_science_prompt(
            task=f"Generate {language} code for the following analysis:\n{query}",
            context=context,
            instruction=(
                "You are a scientific code generation assistant. "
                "Generate clean, well-commented analysis code. "
                "Include proper error handling and logging. "
                "Use standard scientific libraries."
            ),
        )

        result = await self._gateway.structured_output(
            messages=messages,
            schema=_CODE_GENERATION_SCHEMA,
            temperature=0.2,
            max_tokens=4096,
        )

        if result.error:
            logger.warning("Gateway structured output error: %s", result.error)
            return None

        if result.parsed is None:
            logger.warning("Gateway returned no parsed result")
            return None

        code = result.parsed.get("code", "")
        if not code:
            logger.warning("Gateway returned empty code")
            return None

        return CodeSuggestion(
            code=code,
            language=language,
            description=result.parsed.get("description", ""),
            packages_needed=result.parsed.get("packages_needed", []),
            estimated_runtime=result.parsed.get("estimated_runtime", "unknown"),
            confidence=0.7,
        )

    @staticmethod
    def _build_fallback(query: str, language: str) -> CodeSuggestion:
        if language.lower() == "r":
            return CodeSuggestion(
                language="r",
                description=f"Custom R analysis for: {query}",
                packages_needed=[],
                estimated_runtime="unknown",
                confidence=0.3,
                code=f"""\
# R analysis: {query}
# TODO: implement analysis based on the query above
library(tidyverse)

# Load your data
# data <- read.csv("your_data.csv")

# Perform analysis
# summary(data)

# Visualize results
# ggplot(data, aes(x = var1, y = var2)) +
#   geom_point() +
#   theme_minimal()
""",
            )

        return CodeSuggestion(
            language="python",
            description=f"Custom Python analysis for: {query}",
            packages_needed=["pandas", "numpy", "matplotlib"],
            estimated_runtime="unknown",
            confidence=0.3,
            code=f"""\
import logging
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

# Analysis: {query}
# TODO: implement analysis based on the query above

# Load data
# df = pd.read_csv("your_data.csv")

# Perform analysis
# print(df.describe())

# Visualize
# fig, ax = plt.subplots(figsize=(8, 6))
# ax.hist(df["column"], bins=30)
# ax.set_title("Distribution")
# plt.savefig("analysis_output.png", dpi=150)
# plt.show()
""",
        )
