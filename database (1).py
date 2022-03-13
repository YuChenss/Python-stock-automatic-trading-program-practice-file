import time
import sqlite3
import requests
import datetime
import pandas as pd
import shioaji as sj
from trading_calendars import get_calendar


def retry_requests(url, payloads, headers):
    
    for i in range(3):
        try:
            return requests.get(url, params=payloads, headers=headers)
        except:
            print('發生錯誤，等待1分鐘後嘗試')
            time.sleep(60)
    
    return None


def get_daily_prices(date, connection):

    try:
        df = pd.read_sql("select * from daily_prices where 日期 = '{}'".format(date),
                         connection,
                         parse_dates=['日期'],
                         index_col=['證券代號', '日期'])
    except:
        df = pd.DataFrame()
    
    if not df.empty:
        return df, True
    
    url = 'https://www.twse.com.tw/exchangeReport/MI_INDEX'
    
    payloads = {
        'response': 'html',
        'date': date.strftime('%Y%m%d'),
        'type': 'ALLBUT0999'
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.80 Safari/537.36'
    }
    
    response = retry_requests(url, payloads, headers)
    
    try:
        df = pd.read_html(response.text)[-1]
    except:
        print('{} 找不到資料'.format(date.strftime('%Y%m%d')))
        return None, False
    
    df.columns = df.columns.get_level_values(2)
    
    df['漲跌價差'] = df['漲跌價差'].where(df['漲跌(+/-)'] != '-', - df['漲跌價差'])
    
    df.drop(['證券名稱', '漲跌(+/-)'], inplace=True, axis=1)
    
    df['日期'] = pd.to_datetime(date)
    
    df = df.set_index(['證券代號', '日期'])
    
    df = df.apply(pd.to_numeric, errors='coerce')
    
    df.drop(df[df['收盤價'].isnull()].index, inplace=True)
    
    df['昨日收盤價'] = df['收盤價'] - df['漲跌價差']
    
    df['股價振幅'] = (df['最高價'] - df['最低價']) / df['昨日收盤價'] * 100 
    
    return df, False


def update_daily_prices(start_date, end_date, connection):
    
    tw_calendar = get_calendar('XTAI')
    
    main_df = pd.DataFrame()

    for date in pd.date_range(start_date, end_date):
        
        if date not in tw_calendar.opens:
            continue
        
        df, in_db = get_daily_prices(date, connection)

        if df is not None and not in_db:
            main_df = main_df.append(df, sort=False)
            print('{} 每日收盤行情抓取完成，等待15秒'.format(date.strftime('%Y%m%d')))
            time.sleep(15)
            
    if not main_df.empty:
        main_df.to_sql('daily_prices', connection, if_exists='append')
        return main_df


def get_stocks(date, connection):
    
    tw_calendar = get_calendar('XTAI')
    
    prev_trading_date = tw_calendar.previous_close(date).date()
    
    df = pd.read_sql('SELECT * FROM daily_prices WHERE 日期 = "{} 00:00:00"'.format(prev_trading_date),
                     connection, parse_dates=['日期'])
    
    codes = df[(df['收盤價'] > 5) & 
                 (df['收盤價'] < 30) &
                 (df['股價振幅'] > 4) &
                (df['成交股數'] > 50000000)]['證券代號'].tolist()
    
    return codes


def get_stock(code, connection, api):
    
    try:
        sql = "SELECT * FROM stocks WHERE code = '{}'".format(code)
        df = pd.read_sql(sql, connection, index_col=['code'])
    except:
        df = pd.DataFrame()

    if not df.empty:
        return df, True
    
    stock = api.Contracts.Stocks[code]
    
    stock_dict = {
        'code': [stock.code],
        'name': [stock.name],
        'category': [stock.category],
        'day_trade': [stock.day_trade.value]
    }
    
    df = pd.DataFrame(data=stock_dict)
    df = df.set_index('code')
    
    return df, False


def update_stocks(daily_target, connection, api):
    
    main_df = pd.DataFrame()
    
    code_list = []
    
    for codes in daily_target.values():
        code_list.extend(codes)
        
    code_list = list(set(code_list))
    
    for code in code_list:
        
        df, in_db = get_stock(code, connection, api)
        
        if df is not None and not in_db:
            main_df = main_df.append(df, sort=False)
            time.sleep(1)
        
    if not main_df.empty:
        main_df.to_sql('stocks', connection, if_exists='append')
        return main_df
    
    
def get_ticks(code, date, connection, api):
    
    try:
        sql = "SELECT * FROM ticks WHERE code = '{}' and ts BETWEEN '{}' AND '{}'".format(code,
                                                                                          date,
                                                                                          date + 
                                                                                          datetime.timedelta(days=1))
        df = pd.read_sql(sql, connection, parse_dates=['ts'], index_col=['ts'])
    except:
        df = pd.DataFrame()
    
    if not df.empty:
        return df, True
    
    ticks = api.ticks(api.Contracts.Stocks[code], date.strftime('%Y-%m-%d'))
    df = pd.DataFrame({**ticks})
    
    df.ts = pd.to_datetime(df.ts)
    df['code'] = code
    df = df.set_index('ts')

    return df, False


def update_ticks(daily_target, connection, api):
    
    main_df = pd.DataFrame()
    
    tw_calendar = get_calendar('XTAI')
    
    for date, codes in daily_target.items():

        day_trading_codes = [code for code in codes if get_stock(code, connection, api)[0].iloc[0]['day_trade'] == 'Yes']
        
        print('正在更新{}逐筆成交資料，總共{}檔標的'.format(date.strftime('%Y/%m/%d'), len(day_trading_codes)))
        
        for code in day_trading_codes:
            
            df, in_db = get_ticks(code, date, connection, api)
            
            if df is not None and not in_db:
                main_df = main_df.append(df, sort=False)
                time.sleep(1)
            
            prev_trading_date = tw_calendar.previous_close(date).date()
            prev_df, prev_in_db = get_ticks(code, prev_trading_date, connection, api)
            
            if prev_df is not None and not prev_in_db:
                main_df = main_df.append(prev_df, sort=False)
                time.sleep(1)
    
    if not main_df.empty:
        main_df.to_sql('ticks', connection, if_exists='append')
        return main_df
    

def update_historial_data(start_date, end_date, connection, api):
    
    tw_calendar = get_calendar('XTAI')
    
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    
    print('正在更新每日收盤行情...')
    
    update_daily_prices(tw_calendar.previous_close(start).date(), end, connection)
    
    print('正在篩選每日當沖標的...')
    
    daily_target = {}
    
    for date in pd.date_range(start, end):

        if date not in tw_calendar.opens:
            continue
        
        elif (
            date == datetime.datetime.now().date() and
            datetime.datetime.now().hour < 15
        ):
            break
        
        codes = get_stocks(date, connection)
        
        daily_target[date] = codes
    
    print('正在更新股票資訊...') 
    update_stocks(daily_target, connection, api)
    
    print('正在更新逐筆成交資料...')
    update_ticks(daily_target, connection, api)  
    
    print('股市資料庫更新完成')