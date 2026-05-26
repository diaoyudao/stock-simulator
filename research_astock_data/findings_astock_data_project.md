# A-Stock Data Research Summary

## Key Findings

### Purpose and Design
- Full-stack A-share data toolkit for AI coding assistants
- 7-layer architecture with 28 endpoints covering all major data types
- Direct API connections to 13 data sources (no akshare dependency)

### Tech Stack
- Python with 4 core packages: mootdx, requests, pandas, stockstats
- Self-contained Skill file (Markdown + embedded Python)
- No Docker or complex deployment required

### Data Sources
1. mootdx (TCP) - Market data
2. Tencent Finance (HTTP) - Valuation data
3. East Money - Multiple APIs
4. iwencai (OpenAPI) - Natural language search
5. TongHuaShun - Signals and analysis
6. Baidu Finance - Concept blocks and fund flow
7. Sina Finance - Financial statements
8. Cailian Press - News
9. cninfo - Filings

### Architecture
- TCP for real-time market data (mootdx)
- HTTP for all other APIs
- No WebSocket implementation
- Simple file-based caching

### Project Stats
- Stars: 1,249
- Forks: 284
- Active development (V3.0 released May 17, 2026)
- Single maintainer (simonlin1212)

### Assessment
**Can replace Sina/Tencent/AKShare?** YES, with caveats:
✅ Comprehensive coverage of A-share data
✅ Direct API access (more reliable than wrappers)
✅ Easy integration with existing Python code
❌ No real-time streaming
❌ Single maintainer risk
❌ China-focused only

### Recommendation
Excellent choice for StockSimulator data backend, especially for:
- Historical and real-time market data
- Research report retrieval
- Signal and sentiment analysis
- Fundamental data collection
## Sources:
- GitHub Repository: https://github.com/simonlin1212/a-stock-data
- README: https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/README.md
- Changelog: https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/CHANGELOG.md
