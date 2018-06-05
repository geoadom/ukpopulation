import os.path
import json
import zipfile
import numpy as np
import pandas as pd
import requests
from openpyxl import load_workbook
import ukcensusapi.Nomisweb as Api
import ukpopulation.utils as utils

def _read_cell_range(worksheet, topleft, bottomright):
  data_rows = []
  for row in worksheet[topleft:bottomright]:
    data_cols = []
    for cell in row:
      data_cols.append(cell.value)
    data_rows.append(data_cols)
  return np.array(data_rows)

def _country(lad_code):
  lookup={"E": "en", "W": "wa", "S": "sc", "N": "ni"}
  return [lookup[lad_code[0]]]

class SNPPData:
  """
  Functionality for downloading and collating UK Subnational Population Projection (NPP) data
  Nomisweb stores the England data (only)
  Wales/Scotland/NI are not the responsiblity of ONS and are made avilable online by the relevant statistical agency
  """  

  def __init__(self, cache_dir=None):
    if cache_dir is None:
      cache_dir = utils.default_cache_dir()
    self.cache_dir = cache_dir
    self.data_api = Api.Nomisweb(self.cache_dir) 

    self.data = self.__do_england().append(self.__do_wales()).append(self.__do_scotland()).append(self.__do_nireland())

    # LADs * 26 years * 91 ages * 2 genders
    #assert len(self.data) == (326+22+32+11) * 26 * 91 * 2

  def min_year(self):
    """
    Returns the first year in the projection
    """
    return min(self.data.PROJECTED_YEAR_NAME.unique())

  def max_year(self):
    """
    Returns the final year in the projection
    """
    return max(self.data.PROJECTED_YEAR_NAME.unique())

  def filter(self, geog_codes, years=None, ages=range(0,91), genders=[1,2]):

    # convert geog_codes and years to arrays if single values supplied (for isin)
    if isinstance(geog_codes, str):
      geog_codes = [geog_codes]

    years = utils.trim_range(years, self.min_year(), self.max_year())

    # apply filters
    return self.data[(self.data.GEOGRAPHY_CODE.isin(geog_codes)) & 
                     (self.data.PROJECTED_YEAR_NAME.isin(years)) &
                     (self.data.C_AGE.isin(ages)) &
                     (self.data.GENDER.isin(genders))].reset_index(drop=True)

  def aggregate(self, categories, geog_codes, years=None, ages=range(0,91), genders=[1,2]):

    data = self.filter(geog_codes, years, ages, genders)

    # invert categories (they're the ones to aggregate, not preserve)
    return data.groupby(utils.check_and_invert(categories))["OBS_VALUE"].sum().reset_index()

  # For now one LAD at a time (due to multiple countries)
  # For now allow extrapolation of years already in data
  # Filtering age and gender is not (currently) supported
  def extrapolate(self, npp, geog_code, year_range):

    (in_range, ex_range) = utils.split_range(year_range, self.max_year())

    all_years = self.filter(geog_code, in_range)

    for year in ex_range:
      data = self.filter([geog_code], [self.max_year()])
      scaling = npp.year_ratio("ppp", _country(geog_code), self.max_year(), year)
      assert(len(data == len(scaling)))
      data.OBS_VALUE = data.OBS_VALUE * scaling.OBS_VALUE
      data.PROJECTED_YEAR_NAME = year
      all_years = all_years.append(data, ignore_index=True)

    return all_years

  def extrapolagg(self, categories, npp, geog_code, year_range):
    """
    Extrapolate and then aggregate
    """
    data = self.extrapolate(npp, geog_code, year_range)

    # invert categories (they're the ones to aggregate, not preserve)
    return data.groupby(utils.check_and_invert(categories))["OBS_VALUE"].sum().reset_index()

  def create_variant(self, variant_name, npp, geog_code, year_range):
    """
    Apply NPP variant to SNPP: SNPP(v) = SNPP(0) * sum(a,g) [ NPP(v) / NPP(0) ]
    Preserves age-gender structure of SNPP data
    """  
    # split out any years prior to the NPP data (currently SNPP is 2014 based but NPP is 2016)
    (pre_range, in_range) = utils.split_range(year_range, npp.min_year() - 1)
    # for any years prior to NPP we just use the SNPP data as-is (i.e. "ppp")
    pre_data = self.filter(geog_code, pre_range)
    if len(pre_data) > 0:
      print("WARNING: variant {} not applied for years {} that predate the NPP data".format(variant_name, pre_range))

    # return if there's nothing in the NPP range
    if not in_range:
      return pre_data

    data = self.extrapolate(npp, geog_code, in_range).sort_values(["C_AGE", "GENDER", "PROJECTED_YEAR_NAME"]).reset_index(drop=True)

    scaling = npp.variant_ratio(variant_name, _country(geog_code), year_range).reset_index().sort_values(["C_AGE", "GENDER", "PROJECTED_YEAR_NAME"])
    #scaling.to_csv(variant_name + ".csv", index=False)

    #print("DF: ", len(data), ":", len(scaling))
    assert(len(data) == len(scaling))
    data.OBS_VALUE = data.OBS_VALUE * scaling.OBS_VALUE
    
    # prepend any pre-NPP data
    return pre_data.append(data)

  # nomisweb data is now 2016-based
  # def __do_england(self):
  #   print("Collating SNPP data for England...")

  #   # need to do this in 2 batches as entire table has >1000000 rows
  #   table_internal = "NM_2006_1" # 2014-based SNPP
  #   query_params = {
  #     "gender": "1,2",
  #     "c_age": "101...191",
  #     "MEASURES": "20100",
  #     "date": "latest", # 2014-based
  #     "projected_year": "2014...2027",
  #     "select": "geography_code,projected_year_name,gender,c_age,obs_value",
  #     "geography": "1946157057...1946157382"
  #   }
  #   snpp_e = self.data_api.get_data(table_internal, query_params)

  #   query_params["projected_year"] = "2028...2039"
  #   snpp_e = snpp_e.append(self.data_api.get_data(table_internal, query_params))
  #   # make age actual year
  #   snpp_e.C_AGE = snpp_e.C_AGE - 101

  #   #assert(len(snpp_e) == 26*2*91*326) # 326 LADs x 91 ages x 2 genders x 26 years
  #   return snpp_e
  def __do_england(self):
    print("Collating SNPP data for England...")
    england_src = "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/populationandmigration/populationprojections/datasets/localauthoritiesinenglandz1/2014based/snppz1population.zip"
    england_raw = self.cache_dir + "/snpp_e.csv"
    england_zip = self.cache_dir + "/snpp_e.zip"

    if os.path.isfile(england_raw): 
      snpp_e = pd.read_csv(england_raw)
    else: 
      response = requests.get(england_src)
      with open(england_zip, 'wb') as fd:
        for chunk in response.iter_content(chunk_size=1024):
          fd.write(chunk)
        print("Downloaded", england_zip)

      z = zipfile.ZipFile(england_zip)
      #print(z.namelist())  

      snpp_e = pd.DataFrame()
      for gender in [1,2]:
        filename = "2014 SNPP Population "+("males" if gender==1 else "females")+".csv"
        chunk = pd.read_csv(z.open(filename)
        ).drop(["AREA_NAME", "COMPONENT", "SEX"], axis=1
        ).query('AGE_GROUP != "All ages"' 
        #).AGE_GROUP.replace({"90 and over": "90"}
        )
        chunk.AGE_GROUP = chunk.AGE_GROUP.replace({"90 and over": "90"})
        chunk = chunk.melt(id_vars=["AREA_CODE","AGE_GROUP"])
        #chunk = chunk[chunk.AGE_GROUP != "all ages"]
        # chunk = chunk.stack().reset_index() 
        chunk.columns = ["GEOGRAPHY_CODE", "C_AGE", "PROJECTED_YEAR_NAME", "OBS_VALUE"]
        chunk["GENDER"] = gender
        snpp_e = snpp_e.append(chunk)

      #assert(len(snpp_e) == 26*2*91*326) # 326 districts x 91 ages x 2 genders x 26 years
      snpp_e.to_csv(england_raw, index=False)
    return snpp_e

    # Wales
  def __do_wales(self):
    print("Collating SNPP data for Wales...")

    wales_raw = self.cache_dir + "/snpp_w.csv"
    if os.path.isfile(wales_raw): 
      snpp_w = pd.read_csv(wales_raw)
    else:
      fields = ['Area_AltCode1','Year_Code','Data','Gender_Code','Age_Code','Area_Hierarchy','Variant_Code']
      # StatsWales is an OData endpoint, so select fields of interest
      url = "http://open.statswales.gov.wales/dataset/popu5099?$select={}".format(",".join(fields))
      # use OData syntax to filter P (persons), AllAges (all ages), Area_Hierarchy 596 (LADs)
      url += "&$filter=Gender_Code ne 'P' and Area_Hierarchy eq 596 and Variant_Code eq 'Principal'"
      # 
      data = []
      while True:
        print(url)
        r = requests.get(url)
        r_data = r.json()
        data += r_data['value']
        if "odata.nextLink" in r_data:
          url = r_data["odata.nextLink"]
        else:
          break
      snpp_w = pd.DataFrame(data)

      # Remove unwanted and rename wanted columns
      snpp_w = snpp_w.drop(["Area_Hierarchy", "Variant_Code"], axis=1)
      snpp_w = snpp_w.rename(columns={"Age_Code": "C_AGE", 
                                      "Area_AltCode1": "GEOGRAPHY_CODE",
                                      "Data": "OBS_VALUE", 
                                      "Gender_Code": "GENDER", 
                                      "Year_Code": "PROJECTED_YEAR_NAME"})
      # Remove all but SYOA and make numeric 
      snpp_w = snpp_w[(snpp_w.C_AGE!="AllAges") & (snpp_w.C_AGE!="00To15") & (snpp_w.C_AGE!="16To64") & (snpp_w.C_AGE!="65Plus")]
      snpp_w.loc[snpp_w.C_AGE=="90Plus", "C_AGE"] = "90"
      snpp_w.C_AGE = pd.to_numeric(snpp_w.C_AGE)

      # convert gender to census convention 1=M, 2=F
      snpp_w.GENDER = snpp_w.GENDER.map({"M": 1, "F": 2})

      #assert(len(snpp_w) == 26*2*91*22) # 22 LADs x 91 ages x 2 genders x 26 years
      snpp_w.to_csv(wales_raw, index=False)

    return snpp_w

  def __do_scotland(self):
    print("Collating SNPP data for Scotland...")

    scotland_raw = self.cache_dir + "/snpp_s.csv"

    scotland_src = "https://www.nrscotland.gov.uk/files//statistics/population-projections/snpp-2014/detailed/pop-proj-scot-areas-14-det-tab-ca-year.zip"
    scotland_zip = self.cache_dir + "/snpp_s.zip"

    if os.path.isfile(scotland_raw): 
      snpp_s = pd.read_csv(scotland_raw)
    else: 
      response = requests.get(scotland_src)
      with open(scotland_zip, 'wb') as fd:
        for chunk in response.iter_content(chunk_size=1024):
          fd.write(chunk)
        print("Downloaded", scotland_zip)

      z = zipfile.ZipFile(scotland_zip)
      #print(z.namelist())  

      snpp_s = pd.DataFrame()
      for year in range(2014,2040):
        for gender in [1,2]:
          filename = "Population-"+str(year)+("-Male" if gender==1 else "-Female")+".csv"
          chunk = pd.read_csv(z.open(filename)
          ).drop(["Area", "All Ages"], axis=1
          ).drop(0 
          ).rename(columns={"90 and over": "90"}
          ).set_index("Code")

          chunk = chunk.stack().reset_index() 
          chunk.columns = ["GEOGRAPHY_CODE", "C_AGE", "OBS_VALUE"]
          chunk["GENDER"] = gender
          chunk["PROJECTED_YEAR_NAME"] = year
          #print(chunk.head())
          snpp_s = snpp_s.append(chunk)

      print(len(snpp_s))
      #assert(len(snpp_s) == 26*2*91*32) # 32 districts x 91 ages x 2 genders x 26 years
      snpp_s.to_csv(scotland_raw, index=False)
    return snpp_s

  def __do_nireland(self):
    # Niron 
    # (1 worksheet per LAD equivalent)
    print("Collating SNPP data for Northern Ireland...")
    ni_src = "https://www.nisra.gov.uk/sites/nisra.gov.uk/files/publications/SNPP14-LGD14-SYA-1439.xlsx" 
    ni_raw = self.cache_dir + "/snpp_ni.csv"
    if os.path.isfile(ni_raw): 
      snpp_ni = pd.read_csv(ni_raw)
    else:
      response = requests.get(ni_src)
      with open(self.cache_dir + "/ni_raw.xlsx", 'wb') as fd:
        for chunk in response.iter_content(chunk_size=1024):
          fd.write(chunk)

      # easier to hard-code the worksheet names we need (since unlikely to change frequently)
      districts=["Antrim & Newtownabbey",
                "Ards & North Down",
                "Armagh Banbridge & Craigavon",
                "Belfast",
                "Causeway Coast & Glens",
                "Derry & Strabane",
                "Fermanagh & Omagh",
                "Lisburn & Castlereagh",
                "Mid & East Antrim",
                "Mid Ulster",
                "Newry Mourne & Down"]

      xls_ni = load_workbook(self.cache_dir + "/ni_raw.xlsx", read_only=True)

      snpp_ni = pd.DataFrame()

      for d in districts:
        area_code = xls_ni[d]["A2"].value
        males = _read_cell_range(xls_ni[d], "A3", "AA95")
        females = _read_cell_range(xls_ni[d], "A98", "AA190")
        
        dfm = pd.DataFrame(data=males[1:,1:], index=males[1:,0], columns=males[0,1:]).drop(["Age"]).stack().reset_index()
        dfm.columns=["C_AGE", "PROJECTED_YEAR_NAME", "OBS_VALUE"]
        dfm["GENDER"] = pd.Series(1, dfm.index)
        dfm["GEOGRAPHY_CODE"] = pd.Series(area_code, dfm.index)
        dfm.loc[dfm.C_AGE=="90+", "C_AGE"] = "90"

        dff = pd.DataFrame(data=females[1:,1:], index=females[1:,0], columns=females[0,1:]).drop(["Age"]).stack().reset_index()
        dff.columns=["C_AGE", "PROJECTED_YEAR_NAME", "OBS_VALUE"]
        dff["GENDER"] = pd.Series(2, dff.index)
        dff["GEOGRAPHY_CODE"] = pd.Series(area_code, dff.index)
        dff.loc[dff.C_AGE=="90+", "C_AGE"] = 90

        snpp_ni = snpp_ni.append(dfm)
        snpp_ni = snpp_ni.append(dff)

      #assert(len(snpp_ni) == 26*2*91*11) # 11 districts x 91 ages x 2 genders x 26 years
      snpp_ni.to_csv(ni_raw, index=False)

    return snpp_ni