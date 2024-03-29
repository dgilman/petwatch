#!/usr/bin/env python3
import config

import sqlite3
import re
import time
import hashlib
from urllib.parse import urlsplit
from tempfile import NamedTemporaryFile
import datetime
import time
import functools
import filetype

import requests
from requests.adapters import HTTPAdapter, Retry
from lxml import html
import tweepy

TWEET = True
SAVE = True
SLEEP = True

NOW = datetime.datetime.utcnow()
THIRTY_DAYS_AGO = NOW - datetime.timedelta(days=30)


def flip_url(url, path):
    return urlsplit(url)._replace(path=path, query="", fragment="").geturl()


def string2int(string):
    ho = hashlib.sha512()
    ho.update(string.encode("utf-8"))
    return int(ho.hexdigest(), 16) % (10**8)


def adapt_datetime(ts):
    return time.mktime(ts.timetuple())


def convert_datetime(ts):
    return datetime.datetime.fromtimestamp(ts)


sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("DATETIME", convert_datetime)


requests_session = requests.Session()
retries = Retry(total=5, backoff_factor=5, status_forcelist=[500, 502, 503, 504])
requests_session.mount("https://", HTTPAdapter(max_retries=retries))
requests_session.mount("http://", HTTPAdapter(max_retries=retries))


get = functools.partial(requests_session.get, timeout=60)
post = functools.partial(requests_session.post, timeout=60)


class Pet(object):
    def __init__(self, site, site_name, pet_id, pet_name, pet_url, img_srcs):
        self.site = site
        self.site_name = site_name
        self.pet_id = pet_id
        self.pet_name = pet_name
        self.pet_url = pet_url
        self.img_srcs = img_srcs

    def __unicode__(self):
        return f"Pet: {self.site_name} {self.pet_id} {self.pet_name} {self.pet_url} {self.img_srcs}"


class Scraper(object):
    def __init__(self):
        self.conn = sqlite3.connect(config.dbname)
        self.c = self.conn.cursor()
        self.tweepy_auth = tweepy.OAuth1UserHandler(
            config.consumer_key,
            config.consumer_secret,
            config.access_token_key,
            config.access_token_secret,
        )
        self.api = tweepy.API(
            self.tweepy_auth,
            wait_on_rate_limit=True,
            retry_count=5,
            retry_delay=65,
            retry_errors=(500, 502, 503, 504),
        )
        self.api.session = requests_session
        self.client = tweepy.Client(
            consumer_key=config.consumer_key,
            consumer_secret=config.consumer_secret,
            access_token=config.access_token_key,
            access_token_secret=config.access_token_secret,
            wait_on_rate_limit=True,
        )
        self.client.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.client.session.mount("http://", HTTPAdapter(max_retries=retries))

    def seen(self, pet):
        self.c.execute(
            "SELECT 1 FROM seen WHERE site = ? AND pet = ?", (pet.site, pet.pet_id)
        )
        rval = self.c.fetchall()
        if len(rval) == 0:
            return False
        else:
            self.c.execute(
                "UPDATE seen SET seen = ? WHERE site = ? AND pet = ?",
                (NOW, pet.site, pet.pet_id),
            )
            return True

    def save(self, pet):
        self.c.execute(
            "INSERT INTO seen (site, pet, seen) VALUES (?, ?, ?)",
            (pet.site, pet.pet_id, NOW),
        )
        if SAVE:
            self.conn.commit()

    def do_pet(self, pet):
        if self.seen(pet):
            return
        self.tweet(pet)
        self.save(pet)
        if SLEEP:
            time.sleep(5 * 60)

    def tweet(self, pet):
        status = f"{pet.site_name}: {pet.pet_name} {pet.pet_url}"
        if TWEET:
            media_ids = []
            for img_src in pet.img_srcs:
                img_content = get(img_src).content
                if not img_content:
                    continue

                img_type = filetype.guess(img_content)
                if img_type:
                    suffix = f".{img_type.extension}"
                else:
                    raise Exception(f"Unknown file type {img_src}")

                with NamedTemporaryFile(suffix=suffix) as fd:
                    fd.write(img_content)
                    try:
                        media = self.api.media_upload(filename=fd.name)
                    except tweepy.BadRequest as e:
                        if "media type unrecognized." in e.api_errors:
                            print(f"Unknown media type for {img_src}")
                            return
                        raise
                media_ids.append(media.media_id_string)
            self.client.create_tweet(text=status, media_ids=media_ids[:4])
        else:
            print(str(pet), status)

    def end(self):
        self.c.execute(
            "DELETE FROM seen WHERE seen IS NULL or SEEN < ?", (THIRTY_DAYS_AGO,)
        )
        if SAVE:
            self.conn.commit()
        self.conn.close()


class PetHarbor(object):
    PET_ID = re.compile("ID=([^&]*)&")
    PET_NAME = re.compile("My name is (.*)[.]")

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.fromstring(get(url).text)
        pets = et.xpath('//table[@class="ResultsTable"]/tr')[1:]
        [self.do_pet(pet) for pet in pets]

        next_page = et.xpath('//a[text() = "Next Page"]')
        if len(next_page) != 0:
            self.do_page("https://petharbor.com/" + next_page[0].attrib["href"])

    def do_pet(self, pet):
        pet_url = "https://petharbor.com/" + pet[0][0].attrib["href"]
        img = pet[0][0][0]
        img_src = "https://petharbor.com/" + img.attrib["src"]
        pet_id = self.PET_ID.search(img.attrib["src"]).group(1)
        try:
            pet_name = self.PET_NAME.search(pet[1].text).group(1)
        except Exception:
            pet_name = "Unknown"

        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class PetFinder(object):
    PET_ID = re.compile("(\d*)$")

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.fromstring(get(url).text)
        pets = et.xpath('//div[@class="each_pet"]')
        [self.do_pet(pet) for pet in pets]

        np = et.xpath('//div[@class="next_prev"]/span/a')
        if len(np) != 0:
            self.do_page(
                "https://fpm.petfinder.com/petlist/petlist.cgi" + np[0].attrib["href"]
            )

    def _pet_name(self, pet):
        return pet[1][0].text.strip()

    def do_pet(self, pet):
        pet_url = pet[0][0].attrib["href"].strip()
        if not pet_url.startswith("https://"):
            pet_url = "https://" + pet_url

        if len(pet[0][0]) < 2:
            return

        img_src = pet[0][0][1].attrib["src"].strip()
        if not img_src.startswith("https://"):
            img_src = "https://" + img_src
        if "camerashy" in img_src:
            return

        pet_id = self.PET_ID.search(pet_url).group(1)
        pet_name = self._pet_name(pet)

        pet_detail = html.fromstring(get(pet_url).text)
        pet_imgs = pet_detail.xpath(
            '//div[@class="petCarousel-body"]/img[@pfdc-pet-carousel-slide]'
        )
        img_urls = [img.attrib["src"] for img in pet_imgs]

        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_urls)
        self.scraper.do_pet(pet)


class C2CAD(PetFinder):
    def _pet_name(self, pet):
        # Delete the weird A.. thing
        name = pet[1][0].text.strip()
        if name.startswith("A.."):
            return name[3:].strip()
        return name


class Petstablished(object):
    PET_ID = re.compile("/pets/public/(\d+)")

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(get(self.url).text)
        pets = et.xpath('//div[@class="pets"]/div[@class="pet "]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath("div/h2")[0].text.strip()
        pet_url = pet.xpath("div/a")[0].attrib["href"]
        pet_id = int(self.PET_ID.search(pet_url).group(1))
        img_src = pet.xpath("div/a/img")[0].attrib["src"]
        if "defaults" in img_src:
            return
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class Rescuegroups(object):
    PET_ID = re.compile("AnimalID=(\d+)")

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(get(self.url).text)
        pets = et.xpath('//div[@class="animalBrowsePanel"]/div[@class="browse"]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath("div/a/b")[0].text.strip()
        pet_url = flip_url(
            self.url, pet.xpath('div[@class="browsePicture"]/a')[0].attrib["href"]
        )
        pet_id = int(self.PET_ID.search(pet_url).group(1))
        img_obj = pet.xpath("div/a/img")
        if len(img_obj) == 0:
            return
        img_src = img_obj[0].attrib["src"]
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class Grrcc(object):
    PET_ID = re.compile("post-(\d+)")

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(get(self.url).text)
        pets = et.xpath("//article")
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = (
            pet.xpath('descendant::a[@class="cmsms_open_link"]')[0]
            .attrib["title"]
            .strip()
        )
        pet_url = pet.xpath('descendant::a[@class="cmsms_open_link"]')[0].attrib["href"]
        pet_id = int(self.PET_ID.search(pet.attrib["id"]).group(1))
        img_src = pet.xpath('descendant::a[@class="cmsms_image_link"]')[0].attrib[
            "href"
        ]
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class Rescuegroups2(object):
    PET_ID = re.compile("pictures/animals/\d+/(\d+)/\d+_")

    def __init__(self, scraper, site, site_name, pet_url, scrape_url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.pet_url = pet_url
        self.scrape_url = scrape_url

    def run(self):
        et = html.fromstring(get(self.scrape_url).text)
        pets = et.xpath('//td[@class="rgtkSearchResultsCell"]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath(
            'div[contains(concat(" ", @class, " "), " rgtkSearchPetName ")]/a'
        )[0].text
        pet_url = self.pet_url
        img_obj = pet.xpath("div/a/img")
        if len(img_obj) == 0:
            return
        img_src = img_obj[0].attrib["src"]
        pet_re = self.PET_ID.search(img_src)
        if not pet_re:
            return
        pet_id = int(pet_re.group(1))
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class Cabarruscounty(object):
    def __init__(self, scraper, site, site_name, scrape_url, pet_url, animal_type):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.scrape_url = scrape_url
        self.pet_url = pet_url
        self.animal_type = animal_type

    def run(self):
        pets = post(
            self.scrape_url,
            files={"fromSource": (None, "YES"), "animalType": (None, self.animal_type)},
        )
        pets = pets.json()
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet["name"]
        pet_url = self.pet_url
        pet_id = pet["internalID"]
        img_srcs = pet["photoURLs"]
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_srcs)
        self.scraper.do_pet(pet)


class Concordhumane(object):
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.fromstring(get(url).text)
        pets = et.xpath('//div[@class="item-list"]/ul[not(@class="pager")]/li')
        for pet in pets:
            self.do_pet(pet)
        next_link = et.xpath('//li[@class="pager-next"]')
        if len(next_link) != 0:
            self.do_page(flip_url(url, next_link[0][0].attrib["href"]))

    def do_pet(self, pet):
        pet_name = self.get_pet_name(pet)
        pet_url = self.get_pet_url(pet)
        pet_id = string2int(pet_name)
        img_src = self.get_img_src(pet)
        if img_src == None:
            return
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, [img_src])
        self.scraper.do_pet(pet)


class Concordhumanecats(Concordhumane):
    def get_pet_name(self, pet):
        return pet.xpath("span/span/a")[0].text.strip()

    def get_pet_url(self, pet):
        return flip_url(self.url, pet.xpath("div/div/a")[0].attrib["href"])

    def get_img_src(self, pet):
        img_elem = pet.xpath("div/div/a/img")
        if len(img_elem) == 0:
            return
        return img_elem[0].attrib["src"]


class Concordhumanedogs(Concordhumane):
    def get_pet_name(self, pet):
        return pet.xpath("span/a")[0].text.strip().split(" DR")[0]

    def get_pet_url(self, pet):
        return flip_url(self.url, pet.xpath("span/a")[0].attrib["href"])

    def get_img_src(self, pet):
        img_elem = pet.xpath("div/a/img")
        if len(img_elem) == 0:
            return
        return img_elem[0].attrib["src"]


def petwatch():
    scraper = Scraper()

    sites = []
    sites.append(
        PetHarbor(
            scraper,
            1,
            "Charlotte Animal Care & Control",
            "https://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27",
        )
    )
    sites.append(
        PetHarbor(
            scraper,
            2,
            "Charlotte Animal Care & Control",
            "https://petharbor.com/results.asp?WHERE=type_CAT&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            3,
            "Humane Society of York County",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            4,
            "Humane Society of York County",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            5,
            "Greater Charlotte SPCA",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            6,
            "Greater Charlotte SPCA",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            7,
            "Humane Society of Charlotte",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            8,
            "Humane Society of Charlotte",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            9,
            "North Mecklenburg Animal Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            10,
            "North Mecklenburg Animal Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            11,
            "Cornelius Animal Shelter",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            12,
            "Cornelius Animal Shelter",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15",
        )
    )
    sites.append(
        C2CAD(
            scraper,
            13,
            "Catering to Cats & Dogs",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC626&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            14,
            "Furever Angels",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC514&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            15,
            "South Charlotte Dog Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC948&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            16,
            "S.A.F.E Animal Haven",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC371&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            17,
            "MyNextPet.com",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC65&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            18,
            "Richardson Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC113&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            19,
            "Faithful Friends Animal Sanctuary",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC519&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            20,
            "Ruff Life Animal Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC402&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            21,
            "Carolina PAWS",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC516&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        Petstablished(
            scraper,
            22,
            "Piedmont Animal Rescue",
            "https://www.petstablished.com/organization/99949/widget/animals",
        )
    )
    sites.append(
        Petstablished(
            scraper,
            23,
            "Lake Norman Humane",
            "https://www.petstablished.com/organization/30590/widget/animals",
        )
    )
    sites.append(
        Petstablished(
            scraper,
            24,
            "Freedom Farm Rescue",
            "https://www.petstablished.com/organization/55767/widget/animals",
        )
    )
    #    sites.append(PetFinder(scraper, 25, 'Hope for All Dogs', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC834&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(
        Rescuegroups(
            scraper,
            26,
            "Cabarrus Pets Society",
            "https://www.cabarruspets.com/animals/browse",
        )
    )
    sites.append(
        Grrcc(
            scraper,
            27,
            "Golden Retriever Rescue Club of Charlotte",
            "https://grrcc.com/dogs/",
        )
    )
    sites.append(
        Rescuegroups2(
            scraper,
            28,
            "South of the Bully",
            "http://www.southofthebully.com/services.html",
            "https://toolkit.rescuegroups.org/j/3/grid3_layout.php?toolkitKey=2kOov42A",
        )
    )
    sites.append(
        Cabarruscounty(
            scraper,
            29,
            "Cabarrus County Animal Shelter",
            "https://animals.cabarruscounty.us/PHP_SCRIPTS/retrieve_animals.php",
            "https://animals.cabarruscounty.us/avail-dogs.html",
            "availDogs",
        )
    )
    sites.append(
        Cabarruscounty(
            scraper,
            30,
            "Cabarrus County Animal Shelter",
            "https://animals.cabarruscounty.us/PHP_SCRIPTS/retrieve_animals.php",
            "https://animals.cabarruscounty.us/avail-cats.html",
            "availCats",
        )
    )
    sites.append(
        Concordhumanecats(
            scraper,
            31,
            "Humane Society of Concord",
            "https://www.cabarrushumanesociety.org/browse/cat",
        )
    )
    sites.append(
        Concordhumanedogs(
            scraper,
            32,
            "Humane Society of Concord",
            "https://www.cabarrushumanesociety.org/browse/dog",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            33,
            "Charlotte Cocker Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC146&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )
    sites.append(
        PetFinder(
            scraper,
            34,
            "Maggie Lu's Safe Haven Rescue",
            "https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC980&status=A&age=&limit=25&offset=0&animal=&title=&style=15",
        )
    )

    [site.run() for site in sites]
    scraper.end()


def main():
    try:
        petwatch()
    except Exception as e:
        # import pdb

        # pdb.post_mortem()
        import traceback

        tb = traceback.format_exc()

        import smtplib
        import email.mime.text

        msg = email.mime.text.MIMEText(tb)
        msg["Subject"] = "[petwatch] Exception while scraping: {0}".format(str(e))
        msg["From"] = config.email_from
        msg["To"] = config.email_to
        s = smtplib.SMTP("localhost")
        s.sendmail(config.email_from, config.email_to, msg.as_string())
        s.quit()


if __name__ == "__main__":
    main()
