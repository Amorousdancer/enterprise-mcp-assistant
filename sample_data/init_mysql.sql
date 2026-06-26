-- ============================================================
-- 企业级 MCP 智能助理 — MySQL 示例数据库初始化脚本
-- 执行方式: mysql -u root -p < sample_data/init_mysql.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS enterprise_db
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE enterprise_db;

-- -----------------------------------------------------------
-- 1. 员工表
-- -----------------------------------------------------------
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS products;

CREATE TABLE employees (
    employee_id   INT PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(50)  NOT NULL COMMENT '姓名',
    age           INT          NOT NULL COMMENT '年龄',
    department    VARCHAR(50)  NOT NULL COMMENT '部门',
    position      VARCHAR(100) NOT NULL COMMENT '职位',
    salary        DECIMAL(10,2) NOT NULL COMMENT '月薪',
    city          VARCHAR(50)  NOT NULL COMMENT '所在城市',
    hire_date     DATE         NOT NULL COMMENT '入职日期',
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='员工信息表';

INSERT INTO employees (name, age, department, position, salary, city, hire_date) VALUES
('张伟',  28, '技术部',   '高级工程师',  25000.00, '北京', '2021-03-15'),
('李娜',  32, '产品部',   '产品经理',    22000.00, '上海', '2020-06-01'),
('王磊',  26, '技术部',   '前端工程师',  18000.00, '深圳', '2022-01-10'),
('赵敏',  35, '市场部',   '市场总监',    35000.00, '北京', '2019-08-20'),
('刘洋',  29, '技术部',   '后端工程师',  20000.00, '杭州', '2021-09-05'),
('陈静',  31, '财务部',   '财务经理',    28000.00, '上海', '2020-02-14'),
('杨帆',  27, '技术部',   '测试工程师',  16000.00, '广州', '2022-04-18'),
('黄丽',  33, '人力资源部','HR总监',      30000.00, '北京', '2019-11-22'),
('周杰',  25, '技术部',   '算法工程师',  23000.00, '深圳', '2023-01-08'),
('吴芳',  30, '产品部',   'UI设计师',    19000.00, '杭州', '2021-07-12'),
('孙强',  34, '市场部',   '销售经理',    26000.00, '上海', '2020-05-30'),
('朱婷',  28, '技术部',   '数据工程师',  21000.00, '北京', '2022-03-25'),
('何明',  36, '财务部',   '财务总监',    40000.00, '上海', '2018-09-01'),
('林雪',  27, '人力资源部','招聘专员',    15000.00, '广州', '2022-08-15'),
('马超',  31, '技术部',   '架构师',      35000.00, '深圳', '2020-01-20'),
('罗琳',  29, '产品部',   '产品助理',    14000.00, '杭州', '2023-02-28'),
('梁博',  33, '市场部',   '品牌经理',    24000.00, '北京', '2021-04-10'),
('宋佳',  26, '技术部',   '运维工程师',  17000.00, '上海', '2022-06-22'),
('唐亮',  35, '技术部',   '技术总监',    45000.00, '深圳', '2018-07-15'),
('韩梅',  30, '财务部',   '会计',        16000.00, '广州', '2021-10-08'),
('冯涛',  28, '市场部',   '销售代表',    13000.00, '杭州', '2023-03-01'),
('曹颖',  32, '人力资源部','培训经理',    22000.00, '北京', '2020-09-12'),
('邓伟',  27, '技术部',   '安全工程师',  22000.00, '上海', '2022-05-18'),
('许晴',  34, '产品部',   '产品总监',    38000.00, '深圳', '2019-04-25'),
('萧峰',  29, '技术部',   '全栈工程师',  24000.00, '杭州', '2021-11-30');

-- -----------------------------------------------------------
-- 2. 产品表
-- -----------------------------------------------------------
CREATE TABLE products (
    product_id    VARCHAR(20) PRIMARY KEY COMMENT '产品编号',
    product_name  VARCHAR(100) NOT NULL COMMENT '产品名称',
    category      VARCHAR(50)  NOT NULL COMMENT '品类',
    price         DECIMAL(10,2) NOT NULL COMMENT '单价',
    stock         INT          NOT NULL DEFAULT 0 COMMENT '库存',
    supplier      VARCHAR(100) NOT NULL COMMENT '供应商',
    rating        DECIMAL(2,1) DEFAULT 0.0 COMMENT '评分(1-5)',
    launch_date   DATE         COMMENT '上架日期',
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='产品信息表';

INSERT INTO products (product_id, product_name, category, price, stock, supplier, rating, launch_date) VALUES
('P001', 'MacBook Pro 14寸',       '笔记本电脑', 14999.00, 200,  'Apple',     4.8, '2024-01-15'),
('P002', 'ThinkPad X1 Carbon',     '笔记本电脑',  9999.00, 350,  'Lenovo',    4.6, '2024-02-20'),
('P003', 'iPhone 15 Pro',          '手机',        8999.00, 500,  'Apple',     4.7, '2023-09-22'),
('P004', '华为 Mate 60',           '手机',        6999.00, 800,  'Huawei',    4.5, '2023-10-10'),
('P005', 'AirPods Pro 2',          '配件',        1899.00, 1200, 'Apple',     4.6, '2023-09-15'),
('P006', 'Dell U2723QE',           '显示器',      3999.00, 150,  'Dell',      4.4, '2024-01-08'),
('P007', 'HHKB Professional',      '键盘',        2199.00, 80,   'PFU',       4.7, '2023-11-20'),
('P008', 'Logitech MX Master 3S',  '鼠标',         699.00, 600,  'Logitech',  4.5, '2024-03-01'),
('P009', 'Samsung T7 Shield',      '存储',         899.00, 400,  'Samsung',   4.3, '2024-02-15'),
('P010', 'Sony WH-1000XM5',        '耳机',        2499.00, 300,  'Sony',      4.8, '2023-08-10'),
('P011', 'iPad Air M2',            '平板电脑',    4799.00, 250,  'Apple',     4.6, '2024-03-20'),
('P012', '小米14 Ultra',           '手机',        5999.00, 700,  'Xiaomi',    4.4, '2024-02-25'),
('P013', 'ROG 幻16',               '笔记本电脑', 12999.00, 100,  'ASUS',      4.5, '2024-01-30'),
('P014', 'Dyson V15',              '智能家居',    4999.00, 180,  'Dyson',     4.3, '2023-12-01'),
('P015', 'Nintendo Switch OLED',   '游戏机',      2599.00, 450,  'Nintendo',  4.7, '2023-07-15');

-- -----------------------------------------------------------
-- 3. 订单表
-- -----------------------------------------------------------
CREATE TABLE orders (
    order_id      INT PRIMARY KEY AUTO_INCREMENT COMMENT '订单编号',
    customer_name VARCHAR(50)  NOT NULL COMMENT '客户姓名',
    product_id    VARCHAR(20)  NOT NULL COMMENT '产品编号',
    quantity      INT          NOT NULL COMMENT '数量',
    total_amount  DECIMAL(12,2) NOT NULL COMMENT '总金额',
    order_status  VARCHAR(20)  NOT NULL DEFAULT '待付款' COMMENT '订单状态',
    order_date    DATE         NOT NULL COMMENT '下单日期',
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB COMMENT='订单表';

INSERT INTO orders (customer_name, product_id, quantity, total_amount, order_status, order_date) VALUES
('客户A', 'P001', 1, 14999.00,  '已完成', '2024-03-01'),
('客户B', 'P003', 2, 17998.00,  '已完成', '2024-03-02'),
('客户C', 'P005', 3,  5697.00,  '已发货', '2024-03-03'),
('客户D', 'P002', 1,  9999.00,  '已完成', '2024-03-04'),
('客户E', 'P010', 1,  2499.00,  '待付款', '2024-03-05'),
('客户A', 'P011', 1,  4799.00,  '已完成', '2024-03-06'),
('客户F', 'P004', 1,  6999.00,  '已发货', '2024-03-07'),
('客户G', 'P008', 5,  3495.00,  '已完成', '2024-03-08'),
('客户H', 'P015', 1,  2599.00,  '待付款', '2024-03-09'),
('客户B', 'P006', 2,  7998.00,  '已完成', '2024-03-10'),
('客户I', 'P013', 1, 12999.00,  '已发货', '2024-03-11'),
('客户J', 'P007', 2,  4398.00,  '已完成', '2024-03-12'),
('客户C', 'P012', 1,  5999.00,  '已完成', '2024-03-13'),
('客户K', 'P009', 10, 8990.00,  '已发货', '2024-03-14'),
('客户L', 'P014', 1,  4999.00,  '待付款', '2024-03-15');

-- -----------------------------------------------------------
-- 完成
-- -----------------------------------------------------------
SELECT '✅ 数据库初始化完成' AS status;
SELECT COUNT(*) AS employee_count FROM employees;
SELECT COUNT(*) AS product_count FROM products;
SELECT COUNT(*) AS order_count FROM orders;
