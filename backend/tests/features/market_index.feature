# language: zh-CN
功能: 大盘指数
  作为投资者
  我想查看主要大盘指数
  以便了解市场整体走势

  场景: 获取三大指数
    当 我请求大盘指数数据
    那么 返回上证指数、深证成指、创业板指三个指数
    而且 每个指数包含 code、name、current、yesterday、change_pct 字段
    而且 上证指数的 code 为 sh000001
