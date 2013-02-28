#! /usr/bin/env python
# coding:utf-8

from __future__ import division, print_function
import collections
import utility
from smt.ibmmodel import ibmmodel2
from smt.phrase import word_alignment
from smt.phrase import phrase_extract
from progressline import ProgressLine
# import SQLAlchemy
import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine
from sqlalchemy import Column, TEXT, REAL, INTEGER
from sqlalchemy.orm import sessionmaker
import sqlite3


_Base = declarative_base()


class Sentence(_Base):
    __tablename__ = 'sentence'
    id = Column(INTEGER, primary_key=True)
    lang1 = Column(TEXT)
    lang2 = Column(TEXT)


def create_corpus(db="sqlite:///:memory:",
                  lang1method=lambda x: x,
                  lang2method=lambda x: x,
                  limit=None):
    engine = create_engine(db)
    # create session
    Session = sessionmaker(bind=engine)
    session = Session()

    query = session.query(Sentence)[:limit] if limit \
        else session.query(Sentence)

    for item in query:
        yield {"lang1": lang1method(item.lang1),
               "lang2": lang2method(item.lang2)}


def create_train_db(transfrom=2,
                    transto=1,
                    lang1method=lambda x: x,
                    lang2method=lambda x: x,
                    db="sqlite:///:memory:",
                    limit=None,
                    loop_count=1000):
    engine = create_engine(db)
    # create session
    Session = sessionmaker(bind=engine)
    session = Session()

    table_prefix = "from{0}to{1}".format(transfrom, transto)
    wordprob_tablename = table_prefix + "_" + "wordprob"
    wordalign_tablename = table_prefix + "_" + "wordalign"

    _BaseProb = declarative_base()
    _BaseAlign = declarative_base()

    class WordProbability(_BaseProb):
        __tablename__ = wordprob_tablename
        id = Column(INTEGER, primary_key=True)
        transto = Column(TEXT)
        transfrom = Column(TEXT)
        prob = Column(REAL)

    class WordAlignment(_BaseAlign):
        __tablename__ = wordalign_tablename
        id = Column(INTEGER, primary_key=True)
        from_pos = Column(INTEGER)
        to_pos = Column(INTEGER)
        to_len = Column(INTEGER)
        from_len = Column(INTEGER)
        prob = Column(REAL)

    # create table for word probability
    WordProbability.__table__.drop(engine, checkfirst=True)
    WordProbability.__table__.create(engine)
    print("created table: {0}to{1}_wordprob".format(transfrom, transto))

    # create table for alignment probability
    WordAlignment.__table__.drop(engine, checkfirst=True)
    WordAlignment.__table__.create(engine)
    print("created table: {0}to{1}_wordalign".format(transfrom, transto))

    # IBM learning
    with ProgressLine(0.12, title='IBM Model learning...'):
        # check arguments for carete_corpus
        corpus = create_corpus(db=db, limit=limit,
                               lang1method=lang1method,
                               lang2method=lang2method)
        sentences = [(item["lang{0}".format(transto)],
                      item["lang{0}".format(transfrom)])
                     for item in corpus]
        t, a = ibmmodel2.train(sentences=sentences,
                               loop_count=loop_count)
    # insert
    with ProgressLine(0.12, title='Inserting items into database...'):
        for (_to, _from), prob in t.items():
            session.add(WordProbability(transto=_to,
                                        transfrom=_from,
                                        prob=prob))
        for (from_pos, to_pos, to_len, from_len), prob in a.items():
            session.add(WordAlignment(from_pos=from_pos,
                                      to_pos=to_pos,
                                      to_len=to_len,
                                      from_len=from_len,
                                      prob=prob))
        session.commit()


def db_viterbi_alignment(es, fs,
                         transfrom=2,
                         transto=1,
                         db="sqlite:///:memory:",
                         init_val=1.0e-10):
    """
    Calculating viterbi_alignment using specified database.

    Arguments:
        trans:
            it can take "en2ja" or "ja2en"
    """
    engine = create_engine(db)
    # create session
    Session = sessionmaker(bind=engine)
    session = Session()

    table_prefix = "from{0}to{1}".format(transfrom, transto)
    wordprob_tablename = table_prefix + "_" + "wordprob"
    wordalign_tablename = table_prefix + "_" + "wordalign"

    _BaseProb = declarative_base()
    _BaseAlign = declarative_base()

    class WordProbability(_BaseProb):
        __tablename__ = wordprob_tablename
        id = Column(INTEGER, primary_key=True)
        transto = Column(TEXT)
        transfrom = Column(TEXT)
        prob = Column(REAL)

    class WordAlignment(_BaseAlign):
        __tablename__ = wordalign_tablename
        id = Column(INTEGER, primary_key=True)
        from_pos = Column(INTEGER)
        to_pos = Column(INTEGER)
        to_len = Column(INTEGER)
        from_len = Column(INTEGER)
        prob = Column(REAL)

    def get_wordprob(e, f, init_val=1.0e-10):

        query = session.query(WordProbability).filter_by(transto=e,
                                                         transfrom=f)
        try:
            return query.one().prob
        except sqlalchemy.orm.exc.NoResultFound:
            return init_val

    def get_wordalign(i, j, l_e, l_f, init_val=1.0e-10):

        query = session.query(WordAlignment).filter_by(from_pos=i,
                                                       to_pos=j,
                                                       to_len=l_e,
                                                       from_len=l_f)
        try:
            return query.one().prob
        except sqlalchemy.orm.exc.NoResultFound:
            return init_val

    # algorithm
    max_a = collections.defaultdict(float)
    l_e = len(es)
    l_f = len(fs)
    for (j, e) in enumerate(es, 1):
        current_max = (0, -1)
        for (i, f) in enumerate(fs, 1):
            val = get_wordprob(e, f, init_val=init_val) *\
                get_wordalign(i, j, l_e, l_f, init_val=init_val)
            # select the first one among the maximum candidates
            if current_max[1] < val:
                current_max = (i, val)
        max_a[j] = current_max[0]
    return max_a


def db_show_matrix(es, fs,
                   transfrom=2,
                   transto=1,
                   db="sqlite:///:memory:",
                   init_val=0.00001):
    '''
    print matrix according to viterbi alignment like
          fs
     -------------
    e|           |
    s|           |
     |           |
     -------------
    >>> sentences = [("僕 は 男 です", "I am a man"),
                      ("私 は 女 です", "I am a girl"),
                      ("私 は 先生 です", "I am a teacher"),
                      ("彼女 は 先生 です", "She is a teacher"),
                      ("彼 は 先生 です", "He is a teacher"),
                      ]
    >>> t, a = train(sentences, loop_count=1000)
    >>> args = ("私 は 先生 です".split(), "I am a teacher".split(), t, a)
    |x| | | |
    | | |x| |
    | | | |x|
    | | |x| |
    '''
    max_a = db_viterbi_alignment(es, fs,
                                 transfrom=transfrom,
                                 transto=transto,
                                 db=db,
                                 init_val=init_val).items()
    m = len(es)
    n = len(fs)
    return utility.matrix(m, n, max_a)


def _db_symmetrization(lang1s, lang2s,
                       init_val=1.0e-10,
                       db="sqlite:///:memory:"):
    '''
    '''
    transfrom = 2
    transto = 1
    trans = db_viterbi_alignment(lang1s, lang2s,
                                 transfrom=transfrom,
                                 transto=transto,
                                 db=db,
                                 init_val=init_val).items()
    rev_trans = db_viterbi_alignment(lang2s, lang1s,
                                     transfrom=transto,
                                     transto=transfrom,
                                     db=db,
                                     init_val=init_val).items()
    return word_alignment.alignment(lang1s, lang2s, trans, rev_trans)


def db_phrase_extract(lang1, lang2,
                      lang1method=lambda x: x,
                      lang2method=lambda x: x,
                      init_val=1.0e-10,
                      db="sqlite:///:memory:"):
    lang1s = lang1method(lang1).split()
    lang2s = lang1method(lang2).split()
    alignment = _db_symmetrization(lang1s, lang2s,
                                   init_val=init_val,
                                   db=db)
    return phrase_extract.phrase_extract(lang1s, lang2s, alignment)


def create_phrase_db(limit=None,
                     lang1method=lambda x: x,
                     lang2method=lambda x: x,
                     init_val=1.0e-10,
                     db="sqlite:///:memory:"):
    engine = create_engine(db)
    # create session
    Session = sessionmaker(bind=engine)
    session = Session()
    table_name = "phrase"

    _Base = declarative_base()

    class Sentence(_Base):
        __tablename__ = 'sentence'
        id = Column(INTEGER, primary_key=True)
        lang1 = Column(TEXT)
        lang2 = Column(TEXT)

    _BasePhrase = declarative_base()

    class Phrase(_BasePhrase):
        __tablename__ = table_name
        id = Column(INTEGER, primary_key=True)
        lang1p = Column(TEXT)
        lang2p = Column(TEXT)

    # create table for word probability
    Phrase.__table__.drop(engine, checkfirst=True)
    Phrase.__table__.create(engine)
    print("created table: phrase")

    query = session.query(Sentence)[:limit] if limit \
        else session.query(Sentence)

    with ProgressLine(0.12, title='extracting phrases...'):
        for item in query:
            lang1 = item.lang1
            lang2 = item.lang2
            print("  ", lang1, lang2)
            phrases = db_phrase_extract(lang1, lang2,
                                        lang1method=lang1method,
                                        lang2method=lang2method,
                                        init_val=init_val,
                                        db=db)
            for lang1ps, lang2ps in phrases:
                lang1p = u" ".join(lang1ps)
                lang2p = u" ".join(lang2ps)
                ph = Phrase(lang1p=lang1p, lang2p=lang2p)
                session.add(ph)
            session.commit()


# create views using SQLite3
def create_phrase_count_view(db="sqlite:///:memory:"):
    # create phrase_count table
    table_name = "phrasecount"
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        cur.execute("drop view {0}".format(table_name))
    except sqlite3.Error:
        print("{0} view does not exists.\n\
              => creating a new view".format(table_name))
    cur.execute("""create view {0}
                 as select *, count(*) as count from
                phrase group by lang1p, lang2p order by count
                desc""".format(table_name))
    con.commit()

    # create phrase_count_ja table
    table_name_ja = "lang1_phrasecount"
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        cur.execute("drop view {0}".format(table_name_ja))
    except sqlite3.Error:
        print("{0} view does not exists.\n\
              => creating a new view".format(table_name_ja))
    cur.execute("""create view {0}
                as select lang1p as langp,
                sum(count) as count from phrasecount group by
                lang1p order
                by count desc""".format(table_name_ja))
    con.commit()

    # create phrase_count_en table
    table_name_en = "lang2_phrasecount"
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        cur.execute("drop view {0}".format(table_name_en))
    except sqlite3.Error:
        print("{0} view does not exists.\n\
              => creating a new view".format(table_name_en))
    cur.execute("""create view {0}
                as select lang2p as langp,
                sum(count) as count from phrasecount group by
                lang2p order
                by count desc""".format(table_name_en))
    con.commit()


# using sqlite
def create_phrase_prob(transfrom=2, transto=1, db="sqlite:///:memory:"):
    """
    """
    # create phrase_prob table
    table_name = "from{0}to{1}_phraseprob".format(transfrom, transto)
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        print('drop table "{0}"'.format(table_name))
        cur.execute("drop table {0}".format(table_name))
    except sqlite3.Error:
        print("{0} table does not exists.\n\
              => creating a new table".format(table_name))
    cur.execute("""create table {0}
                (transtop TEXT, transfromp TEXT,
                prob REAL)""".format(table_name))
    con.commit()

    cur_sel = con.cursor()
    cur_rec = con.cursor()
    cur.execute("select lang1p, lang2p, count from phrasecount")
    with ProgressLine(0.12, title='phrase learning...'):
        for lang1p, lang2p, count in cur:
            if transfrom == 2 and transto == 1:
                cur_sel.execute(u"""select count
                                from lang1_phrasecount where
                                langp=?""",
                                (lang1p,))
                count_e_j = list(cur_sel)
                count_e_j = count_e_j[0][0]
                prob = count / count_e_j
                cur_rec.execute(u"""insert into {0} values
                                (?, ?, ?)""".format(table_name),
                                (lang1p, lang2p, prob))
                print(u"{0} => {1} : {2}".format(lang1p, lang2p, prob))
            elif transfrom == 1 and transto == 2:
                cur_sel.execute(u"""select count
                                from lang2_phrasecount where
                                langp=?""",
                                (lang2p,))
                count_e_j = list(cur_sel)
                count_e_j = count_e_j[0][0]
                prob = count / count_e_j
                cur_rec.execute(u"""insert into {0} values
                                (?, ?, ?)""".format(table_name),
                                (lang2p, lang1p, prob))
                print(u"{0} => {1} : {2}".format(lang2p, lang1p, prob))
            con.commit()
            con.commit()
            # I must implement ja2en ver.
    con.commit()


def createdb(db=":memory:",
             lang1method=lambda x: x,
             lang2method=lambda x: x,
             init_val=1.0e-10,
             limit=None,
             loop_count=1000,
             ):
    sqlalchemydb = "sqlite:///{0}".format(db)
    create_train_db(transfrom=2,
                    transto=1,
                    db=sqlalchemydb,
                    limit=limit,
                    loop_count=loop_count,
                    lang1method=lang1method,
                    lang2method=lang2method)
    create_train_db(transfrom=1,
                    transto=2,
                    db=sqlalchemydb,
                    limit=limit,
                    loop_count=loop_count,
                    lang1method=lang1method,
                    lang2method=lang2method)
    create_phrase_db(db=sqlalchemydb, limit=limit,
                     lang1method=lang1method,
                     lang2method=lang2method,
                     init_val=init_val)
    create_phrase_count_view(db)
    create_phrase_prob(transfrom=2, transto=1, db=db)
    create_phrase_prob(transfrom=1, transto=2, db=db)

if __name__ == "__main__":
    pass
    #create_train_db(trans="en2ja",
    #                db_name=":jec_basic:",
    #                limit=None,
    #                loop_count=1000)
    #create_train_db(trans="ja2en",
    #                db_name=":jec_basic:",
    #                limit=None,
    #                loop_count=1000)
    #create_phrase_db(db_name=":jec_basic:", limit=None)
    #create_phrase_count_view(db_name=":jec_basic:")