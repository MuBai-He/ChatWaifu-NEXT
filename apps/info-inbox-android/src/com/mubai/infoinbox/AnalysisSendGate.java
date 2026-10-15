package com.mubai.infoinbox;

/** Checks local authorization immediately before writing a request body. */
public interface AnalysisSendGate {
  interface Write {
    void run() throws Exception;
  }

  void send(Write write) throws Exception;

  AnalysisSendGate DIRECT = Write::run;
}
